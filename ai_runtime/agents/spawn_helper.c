/*
 * decentai-spawn — the one program in the runtime's image that may start
 * a process as another user (docs/system/sandbox.md).
 *
 * The runtime is an ordinary user and cannot do this itself. It starts
 * this program where it used to start a worker, with the same pipes,
 * and this program becomes the worker — as the agent's own user, with
 * limits set and with the right to gain privileges given up for good.
 *
 *   decentai-spawn check
 *       Whether users can be switched here. Exit 0 when they can.
 *
 *   decentai-spawn own <user> <folder>
 *       Hand a worker's folder to its user. <folder> is
 *       <workers>/<name>/home (the user's alone) or
 *       <workers>/<name>/spool (the user's, shared with the runtime).
 *
 *   decentai-spawn clear <folder>
 *       Delete what is inside a worker's folder, as the user it
 *       belongs to.
 *
 *   decentai-spawn stop <user>
 *       End every process of that user.
 *
 *   decentai-spawn sweep <user>
 *       Delete what that user left in the folders every user may write
 *       to (/tmp, /dev/shm), as that user.
 *
 *   decentai-spawn run <user> <home> <processes> <open-files> <file-bytes>
 *                      [r:<path> | w:<path>]... -- <program> [arguments...]
 *       Become <user> and then <program>. With paths named, the
 *       program and everything it starts may read and run what is
 *       under an r: path, do anything under a w: path, and open
 *       nothing else (Landlock). A path that is not there is passed
 *       over; a kernel that cannot do this is a refusal.
 *
 * What it refuses, whoever asks: a caller that is not the runtime's
 * user, a user outside the range kept for workers, and a folder that is
 * not one of the two a worker has. It is granted its rights through
 * file capabilities, and a process it has started can never use it:
 * that process gave up gaining privileges before it began.
 *
 * Exit status: 0 done, 126 refused or failed (the reason is on stderr).
 * `run` does not return; it is the program it was asked to become.
 */

#define _GNU_SOURCE

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <linux/capability.h>
#include <linux/landlock.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

/* Set when the image is built; the defaults are the image's own. */
#ifndef DECENTAI_RUNTIME_UID
#define DECENTAI_RUNTIME_UID 999
#endif
#ifndef DECENTAI_RUNTIME_GID
#define DECENTAI_RUNTIME_GID 999
#endif
#ifndef DECENTAI_WORKERS_DIR
#define DECENTAI_WORKERS_DIR "/data/agents/workers"
#endif

#define FIRST_USER 20000
#define LAST_USER 29999
#define REFUSED 126
#define NAME_MAX_LENGTH 64
#define CLEAR_MAX_DEPTH 64
#define FOLDER_FLAGS (O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
/* A worker's folder is closed to everyone but the worker, the runtime
 * included. It is opened as a place and not for reading: enough to see
 * whose it is and to hand it over, which is all that is done with it
 * before its owner's own rights are taken on. */
#define PLACE_FLAGS (O_PATH | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)

/* Landlock's calls have no wrapper in the C library, and its rights
 * grew with the kernel: a header older than the kernel it runs on
 * would not know the later ones. */
#ifndef SYS_landlock_create_ruleset
#define SYS_landlock_create_ruleset 444
#define SYS_landlock_add_rule 445
#define SYS_landlock_restrict_self 446
#endif
#ifndef LANDLOCK_ACCESS_FS_REFER
#define LANDLOCK_ACCESS_FS_REFER (1ULL << 13)
#endif
#ifndef LANDLOCK_ACCESS_FS_TRUNCATE
#define LANDLOCK_ACCESS_FS_TRUNCATE (1ULL << 14)
#endif

/* Every right the first version knows. */
#define FENCED_AT_FIRST ( \
    LANDLOCK_ACCESS_FS_EXECUTE | LANDLOCK_ACCESS_FS_WRITE_FILE | \
    LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_READ_DIR | \
    LANDLOCK_ACCESS_FS_REMOVE_DIR | LANDLOCK_ACCESS_FS_REMOVE_FILE | \
    LANDLOCK_ACCESS_FS_MAKE_CHAR | LANDLOCK_ACCESS_FS_MAKE_DIR | \
    LANDLOCK_ACCESS_FS_MAKE_REG | LANDLOCK_ACCESS_FS_MAKE_SOCK | \
    LANDLOCK_ACCESS_FS_MAKE_FIFO | LANDLOCK_ACCESS_FS_MAKE_BLOCK | \
    LANDLOCK_ACCESS_FS_MAKE_SYM)
#define TO_READ ( \
    LANDLOCK_ACCESS_FS_EXECUTE | LANDLOCK_ACCESS_FS_READ_FILE | \
    LANDLOCK_ACCESS_FS_READ_DIR)
/* What can be said of a file; the rest is said of folders only. */
#define OF_A_FILE ( \
    LANDLOCK_ACCESS_FS_EXECUTE | LANDLOCK_ACCESS_FS_WRITE_FILE | \
    LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_TRUNCATE)

static void refuse(const char *why)
{
    fprintf(stderr, "decentai-spawn: %s\n", why);
    exit(REFUSED);
}

static void fail(const char *what)
{
    fprintf(stderr, "decentai-spawn: %s: %s\n", what, strerror(errno));
    exit(REFUSED);
}

static int is_worker(uid_t user)
{
    return user >= FIRST_USER && user <= LAST_USER;
}

static long number(const char *text, const char *what)
{
    char *end = NULL;
    long value;

    errno = 0;
    value = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || value < 0) {
        fprintf(stderr, "decentai-spawn: %s is not a number: %s\n", what, text);
        exit(REFUSED);
    }
    return value;
}

static uid_t worker(const char *text)
{
    long value = number(text, "the user");

    if (!is_worker((uid_t)value))
        refuse("that user is outside the range kept for workers");
    return (uid_t)value;
}

static int plain_name(const char *name, size_t length)
{
    size_t index;

    if (length == 0 || length > NAME_MAX_LENGTH)
        return 0;
    for (index = 0; index < length; index++) {
        char c = name[index];
        int fine = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
            || (c >= '0' && c <= '9') || c == '_' || c == '-';
        if (!fine)
            return 0;
    }
    return 1;
}

/*
 * Open <workers>/<name>/<home|spool> one step at a time, following no
 * link at any step. `spool` is set to whether it is the spool.
 */
static int worker_folder(const char *path, int *spool)
{
    static const char root[] = DECENTAI_WORKERS_DIR "/";
    char name[NAME_MAX_LENGTH + 1];
    const char *rest, *slash, *kind;
    struct stat about;
    int root_fd, name_fd, folder_fd;
    size_t length;

    if (strncmp(path, root, sizeof(root) - 1) != 0)
        refuse("that folder is not a worker's");
    rest = path + sizeof(root) - 1;
    slash = strchr(rest, '/');
    if (slash == NULL)
        refuse("that folder is not a worker's");
    length = (size_t)(slash - rest);
    if (!plain_name(rest, length))
        refuse("that worker's name is not a plain name");
    memcpy(name, rest, length);
    name[length] = '\0';

    kind = slash + 1;
    if (strcmp(kind, "home") != 0 && strcmp(kind, "spool") != 0)
        refuse("a worker has a home and a spool, and nothing else");
    *spool = strcmp(kind, "spool") == 0;

    root_fd = open(DECENTAI_WORKERS_DIR, FOLDER_FLAGS);
    if (root_fd < 0)
        fail("the workers' folder");
    name_fd = openat(root_fd, name, FOLDER_FLAGS);
    if (name_fd < 0)
        fail("the worker's folder");
    /* The runtime made it and still owns it: a worker cannot have put
     * something of its own in its place. */
    if (fstat(name_fd, &about) != 0)
        fail("the worker's folder");
    if (about.st_uid != DECENTAI_RUNTIME_UID)
        refuse("the worker's folder is not the runtime's");
    folder_fd = openat(name_fd, kind, PLACE_FLAGS);
    if (folder_fd < 0)
        fail(kind);
    close(name_fd);
    close(root_fd);
    return folder_fd;
}

static void give_up_privileges(void)
{
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0)
        fail("giving up new privileges");
}

/*
 * Become `user`, for good: no other group, no capability, and no way
 * back. Anything short of all of it is a failure, not a warning.
 */
static void become(uid_t user, gid_t group)
{
    struct __user_cap_header_struct header;
    struct __user_cap_data_struct none[2];

    if (setgroups(0, NULL) != 0)
        fail("dropping the groups");
    if (setresgid(group, group, group) != 0)
        fail("becoming the group");
    if (setresuid(user, user, user) != 0)
        fail("becoming the user");

    memset(&header, 0, sizeof(header));
    memset(none, 0, sizeof(none));
    header.version = _LINUX_CAPABILITY_VERSION_3;
    if (syscall(SYS_capset, &header, none) != 0)
        fail("dropping the capabilities");

    if (getuid() != user || geteuid() != user
            || getgid() != group || getegid() != group)
        refuse("the user did not change");
    if (setuid(DECENTAI_RUNTIME_UID) == 0 || setuid(0) == 0)
        refuse("the way back was still open");
}

/* Which version of Landlock this kernel has; 0 when it has none. */
static int landlock_version(void)
{
    long version = syscall(SYS_landlock_create_ruleset, NULL, 0,
                           LANDLOCK_CREATE_RULESET_VERSION);

    return version < 0 ? 0 : (int)version;
}

/*
 * Fence this process, and everything it will start, into the paths
 * named. It can only take away: whoever calls it ends with less than
 * they had, so the paths need no checking.
 */
static void fence(int count, char **rules)
{
    struct landlock_ruleset_attr fenced;
    int version = landlock_version();
    int ruleset, index;

    if (version < 1)
        refuse("this kernel cannot fence files (no Landlock)");

    memset(&fenced, 0, sizeof(fenced));
    fenced.handled_access_fs = FENCED_AT_FIRST;
    if (version >= 2)
        fenced.handled_access_fs |= LANDLOCK_ACCESS_FS_REFER;
    if (version >= 3)
        fenced.handled_access_fs |= LANDLOCK_ACCESS_FS_TRUNCATE;

    ruleset = (int)syscall(SYS_landlock_create_ruleset,
                           &fenced, sizeof(fenced), 0);
    if (ruleset < 0)
        fail("making the fence");

    for (index = 0; index < count; index++) {
        struct landlock_path_beneath_attr beneath;
        struct stat about;
        const char *path = rules[index] + 2;
        int opened = open(path, O_PATH | O_CLOEXEC);

        if (opened < 0) {
            if (errno == ENOENT)
                continue;
            fail(path);
        }
        if (fstat(opened, &about) != 0)
            fail(path);

        memset(&beneath, 0, sizeof(beneath));
        beneath.parent_fd = opened;
        beneath.allowed_access = fenced.handled_access_fs;
        if (rules[index][0] == 'r')
            beneath.allowed_access &= TO_READ;
        if (!S_ISDIR(about.st_mode))
            beneath.allowed_access &= OF_A_FILE;
        if (syscall(SYS_landlock_add_rule, ruleset,
                    LANDLOCK_RULE_PATH_BENEATH, &beneath, 0) != 0)
            fail(path);
        close(opened);
    }

    if (syscall(SYS_landlock_restrict_self, ruleset, 0) != 0)
        fail("closing the fence");
    close(ruleset);
}

/* ------------------------------------------------------------------ */

static int check(void)
{
    int status = 0;
    pid_t child = fork();

    if (child < 0)
        fail("fork");
    if (child == 0) {
        give_up_privileges();
        become(FIRST_USER, FIRST_USER);
        _exit(0);
    }
    if (waitpid(child, &status, 0) < 0)
        fail("waitpid");
    if (!WIFEXITED(status) || WEXITSTATUS(status) != 0)
        return REFUSED;
    /* What else the caller may ask for here. */
    printf("landlock=%d\n", landlock_version());
    return 0;
}

/*
 * Owner, group and mode of the folder `place` names — the folder
 * itself, reached through the descriptor, never through its name.
 */
static void hand_over(int place, uid_t user, gid_t group, mode_t mode,
                      const char *what)
{
    char through[64];

    if (fchownat(place, "", user, group, AT_EMPTY_PATH) != 0)
        fail(what);
    snprintf(through, sizeof(through), "/proc/self/fd/%d", place);
    if (chmod(through, mode) != 0)
        fail(what);
}

static int own(const char *user_text, const char *path)
{
    uid_t user = worker(user_text);
    struct stat about;
    int spool = 0;
    int folder = worker_folder(path, &spool);

    if (fstat(folder, &about) != 0)
        fail("the folder");
    if (about.st_uid != DECENTAI_RUNTIME_UID && !is_worker(about.st_uid))
        refuse("that folder belongs to somebody else");

    if (spool) {
        /* The worker's, and the runtime's through the group: each
         * leaves files there for the other to read. */
        hand_over(folder, user, DECENTAI_RUNTIME_GID, 02770, "the spool");
    } else {
        hand_over(folder, user, user, 0700, "the home");
    }
    return 0;
}

static void remove_contents(int folder, int depth)
{
    DIR *listing;
    struct dirent *entry;
    int copy;

    if (depth > CLEAR_MAX_DEPTH)
        refuse("the folder is deeper than anything a worker should make");
    copy = dup(folder);
    if (copy < 0)
        fail("dup");
    listing = fdopendir(copy);
    if (listing == NULL)
        fail("reading the folder");

    while ((entry = readdir(listing)) != NULL) {
        int child;

        if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0)
            continue;
        if (unlinkat(folder, entry->d_name, 0) == 0)
            continue;
        /* Not a file: a folder, which is emptied and then removed.
         * A link was removed above, never followed. */
        child = openat(folder, entry->d_name, FOLDER_FLAGS);
        if (child < 0)
            continue;
        (void)fchmod(child, 0700);
        remove_contents(child, depth + 1);
        close(child);
        (void)unlinkat(folder, entry->d_name, AT_REMOVEDIR);
    }
    closedir(listing);
}

static int clear(const char *path)
{
    struct stat about;
    int spool = 0, inside;
    int folder = worker_folder(path, &spool);

    if (fstat(folder, &about) != 0)
        fail("the folder");
    if (about.st_uid == DECENTAI_RUNTIME_UID)
        return 0;               /* never handed over: nothing of a worker's */
    if (!is_worker(about.st_uid))
        refuse("that folder belongs to somebody else");

    /* As its owner, so that nothing but its owner's is ever deleted. */
    give_up_privileges();
    become(about.st_uid, about.st_uid);
    inside = openat(folder, ".", FOLDER_FLAGS);
    if (inside < 0)
        fail("opening the folder as its owner");
    remove_contents(inside, 0);
    return 0;
}

/*
 * Some programs write to /tmp whatever they are told — a browser keeps
 * its lock there — so a worker is let into the folders every user may
 * write to. What it leaves is its own and closed to other users, and
 * is removed here, so that it does not outlive the worker either.
 */
static int sweep(const char *user_text)
{
    static const char *const shared[] = {"/tmp", "/dev/shm"};
    uid_t user = worker(user_text);
    size_t index;

    give_up_privileges();
    become(user, user);

    for (index = 0; index < sizeof(shared) / sizeof(shared[0]); index++) {
        struct dirent *entry;
        DIR *listing;
        int folder = open(shared[index], FOLDER_FLAGS);

        if (folder < 0)
            continue;
        listing = fdopendir(folder);
        if (listing == NULL) {
            close(folder);
            continue;
        }
        while ((entry = readdir(listing)) != NULL) {
            struct stat about;
            int child;

            if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0)
                continue;
            if (fstatat(folder, entry->d_name, &about, AT_SYMLINK_NOFOLLOW) != 0
                    || about.st_uid != user)
                continue;
            if (unlinkat(folder, entry->d_name, 0) == 0)
                continue;
            child = openat(folder, entry->d_name, FOLDER_FLAGS);
            if (child < 0)
                continue;
            (void)fchmod(child, 0700);
            remove_contents(child, 1);
            close(child);
            (void)unlinkat(folder, entry->d_name, AT_REMOVEDIR);
        }
        closedir(listing);
    }
    return 0;
}

static int stop(const char *user_text)
{
    uid_t user = worker(user_text);

    give_up_privileges();
    become(user, user);
    /* Everything this user may signal is everything this user runs;
     * the caller of kill is not among the signalled. */
    if (kill(-1, SIGKILL) != 0 && errno != ESRCH)
        fail("ending the user's processes");
    return 0;
}

static void limit(int which, long value, const char *what)
{
    struct rlimit bound;

    bound.rlim_cur = bound.rlim_max = (rlim_t)value;
    if (setrlimit(which, &bound) != 0)
        fail(what);
}

static int run(int count, char **arguments)
{
    static const char usage[] =
        "run <user> <home> <processes> <open-files> <file-bytes> "
        "[r:<path> | w:<path>]... -- <program> [arguments...]";
    uid_t user;
    struct stat about;
    int spool = 0, home, rules, program;

    if (count < 8)
        refuse(usage);
    for (rules = 6; rules < count && strcmp(arguments[rules], "--") != 0; rules++) {
        const char *rule = arguments[rules];

        if ((rule[0] != 'r' && rule[0] != 'w') || rule[1] != ':' || rule[2] != '/')
            refuse(usage);
    }
    program = rules + 1;
    if (program >= count)
        refuse(usage);
    if (arguments[program][0] != '/')
        refuse("the program is named by its whole path");

    user = worker(arguments[1]);
    home = worker_folder(arguments[2], &spool);
    if (spool)
        refuse("a worker's home is its home");
    if (fstat(home, &about) != 0)
        fail("the home");
    if (about.st_uid != user)
        refuse("that home is not this user's");

    limit(RLIMIT_NPROC, number(arguments[3], "processes"), "limiting processes");
    limit(RLIMIT_NOFILE, number(arguments[4], "open files"), "limiting open files");
    limit(RLIMIT_FSIZE, number(arguments[5], "file bytes"), "limiting file size");
    limit(RLIMIT_CORE, 0, "limiting core files");

    /* What a worker writes is its own and the runtime's, nobody else's. */
    umask(0007);

    give_up_privileges();
    become(user, user);
    if (rules > 6)
        fence(rules - 6, arguments + 6);

    execv(arguments[program], arguments + program);
    fail(arguments[program]);
    return REFUSED;
}

int main(int count, char **arguments)
{
    const char *command;

    if (getuid() != DECENTAI_RUNTIME_UID)
        refuse("only the runtime may use this");
    if (count < 2)
        refuse("check | own | clear | stop | sweep | run");

    command = arguments[1];
    if (strcmp(command, "check") == 0 && count == 2)
        return check();
    if (strcmp(command, "own") == 0 && count == 4)
        return own(arguments[2], arguments[3]);
    if (strcmp(command, "clear") == 0 && count == 3)
        return clear(arguments[2]);
    if (strcmp(command, "stop") == 0 && count == 3)
        return stop(arguments[2]);
    if (strcmp(command, "sweep") == 0 && count == 3)
        return sweep(arguments[2]);
    if (strcmp(command, "run") == 0)
        return run(count - 1, arguments + 1);
    refuse("check | own | clear | stop | sweep | run");
    return REFUSED;
}
