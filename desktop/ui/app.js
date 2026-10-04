// The DecentAI desktop app's window.
//
// It decides nothing about the install: the native side knows the
// engine and runs the launcher, and this shows what it says and sends
// back what the person chose. Opened in a plain browser, without the
// native side, it runs against PretendBackend — which is how the
// screens are worked on.

/** The native side, as the window speaks to it. */
class Backend {
  constructor(tauri) {
    this.tauri = tauri;
  }

  ask(command, payload) {
    return this.tauri.core.invoke(command, payload || {});
  }

  /** `said(text)` for every line of work under way. */
  onProgress(said) {
    this.tauri.event.listen('progress', (event) => said(String(event.payload || '')));
  }

  /** `changed()` when the tray menu did something the window shows. */
  onChanged(changed) {
    this.tauri.event.listen('changed', () => changed());
  }
}

/** Stands where the native side would, in a plain browser: a computer
 *  with Docker running and nothing installed, where everything takes a
 *  moment and works. `?engine=missing`, `?installed=1` and `?update=1`
 *  in the address start it elsewhere. */
class PretendBackend {
  constructor() {
    const asked = new URLSearchParams(location.search);
    this.engine = asked.get('engine') || 'ready';
    this.installed = asked.has('installed');
    this.running = this.installed && !asked.has('stopped');
    this.update = asked.has('update');
    this.fails = asked.get('fails') || '';
    this.said = () => {};
  }

  onProgress(said) { this.said = said; }
  onChanged() {}

  wait(ms) { return new Promise((done) => setTimeout(done, ms)); }

  async lines(lines) {
    for (const line of lines) {
      this.said(line);
      await this.wait(700);
    }
  }

  async ask(command, payload) {
    await this.wait(250);
    if (command === this.fails) {
      throw 'the stack could not up: port 4280 is already in use';
    }
    switch (command) {
      case 'state':
        return {
          app_version: '0.4.0',
          engine: {
            name: this.engine === 'missing' ? '' : 'Docker',
            ready: this.engine === 'ready',
            missing: this.engine === 'missing',
            can_install: true,
          },
          installed: this.installed,
          running: this.running,
          version: this.installed ? (this.update ? '0.3.0' : '0.4.0') : '',
          address: 'http://localhost:4280',
          first_person: this.installed ? 'sara@example.com' : '',
          update: this.update ? { version: '0.4.0', notes: '' } : null,
        };
      case 'install':
        await this.lines(['Downloading the backend…', 'Downloading the runtime…',
          'Downloading the frontend…', 'Making this install\'s keys…',
          'Preparing the database…', 'Starting…']);
        this.installed = true; this.running = true;
        return null;
      case 'start':
        await this.lines(['Starting…']);
        this.running = true;
        return null;
      case 'stop':
        this.running = false;
        return null;
      case 'update':
        await this.lines(['Downloading the backend…', 'Keeping a copy of the database…',
          'Installing 0.4.0…']);
        this.update = false;
        return null;
      case 'uninstall':
        await this.lines(['Keeping a copy of the database…', 'Removing DecentAI…']);
        this.installed = false; this.running = false;
        return { kept: payload.keep ? 'Documents\\DecentAI backup 2026-10-03.archive.gz' : '' };
      case 'reset_password':
        return null;
      case 'start_engine':
      case 'install_engine':
        await this.lines(['Starting Docker…']);
        this.engine = 'ready';
        return null;
      case 'data':
        return {
          engine: 'Docker',
          places: ['the database: volume decentai-app_mongo_data',
            'uploaded files: volume decentai-app_uploads_data',
            'approved agents: volume decentai-app_agent_packages',
            'this install\'s keys and its backups: volume decentai_launcher'],
        };
      default:
        return null;
    }
  }
}

/** What a first person's email and password must be — the launcher's
 *  own rules (launcher/installation.py), checked here so that a mistake
 *  is said beside the field instead of after a download. */
class FirstPerson {
  static PASSWORD_MIN = 10;

  static rules(password) {
    return {
      length: password.length >= FirstPerson.PASSWORD_MIN,
      letter: /\p{L}/u.test(password),
      number: /\d/.test(password),
    };
  }

  static emailProblem(email) {
    if (!email) return '';
    return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email) ? '' : 'That is not an email address.';
  }

  static againProblem(password, again) {
    if (!again) return '';
    return password === again ? '' : 'The two passwords are not the same.';
  }

  static passwordProblem(password) {
    return password && password.trim() !== password
      ? 'The password cannot start or end with a space.' : '';
  }

  static ready(email, password, again) {
    const rules = FirstPerson.rules(password);
    return Boolean(email) && !FirstPerson.emailProblem(email)
      && rules.length && rules.letter && rules.number
      && !FirstPerson.passwordProblem(password)
      && Boolean(again) && password === again;
  }
}

class App {
  constructor(backend) {
    this.backend = backend;
    this.view = document.getElementById('view');
    this.state = null;
    this.steps = null;
    backend.onProgress((text) => this.said(text));
    backend.onChanged(() => { if (!this.steps) this.look(); });
  }

  // -- screens ---------------------------------------------------------

  /** Put a screen in the view, fill its slots, and wire its buttons. */
  show(name, slots, acts) {
    const screen = document.getElementById(`screen-${name}`).content.cloneNode(true);
    for (const [slot, value] of Object.entries(slots || {})) {
      const place = screen.querySelector(`[data-slot="${slot}"]`);
      if (!place) continue;
      if (value === null) { place.hidden = true; continue; }
      place.hidden = false;
      place.textContent = value;
    }
    for (const button of screen.querySelectorAll('[data-act]')) {
      const act = (acts || {})[button.dataset.act];
      if (act) button.addEventListener('click', () => act(button));
    }
    this.steps = null;
    this.view.replaceChildren(screen);
    const first = this.view.querySelector('input, .actions .btn--primary');
    if (first) first.focus();
    return this.view;
  }

  async look() {
    this.show('looking');
    try {
      this.state = await this.backend.ask('state');
    } catch (failed) {
      return this.failed('This computer could not be looked at.', failed);
    }
    this.settle();
  }

  /** The screen the state calls for. */
  settle() {
    const state = this.state;
    if (!state.engine.ready) return this.engine();
    if (!state.installed) return this.setup();
    return state.running ? this.running() : this.stopped();
  }

  engine() {
    const engine = this.state.engine;
    if (engine.missing) {
      return this.show('engine', {
        title: 'DecentAI needs a container engine',
        text: 'DecentAI runs in containers, which keeps it apart from the rest of this '
          + 'computer. Neither Docker nor Podman is installed here.',
        note: engine.can_install
          ? 'Podman is free and open source. Installing it asks for your permission '
            + 'once, and may restart this computer\'s Linux subsystem.'
          : 'Install Docker Desktop or Podman, then look again.',
      }, {
        'engine-primary': (button) => this.work(
          'Installing Podman', 'install_engine', {}, button),
        'look-again': () => this.look(),
      }).querySelector('[data-act="engine-primary"]').textContent = 'Install Podman';
    }
    this.show('engine', {
      title: `${engine.name} is not running`,
      text: `DecentAI runs on ${engine.name}, which is installed here and stopped.`,
      note: '',
    }, {
      'engine-primary': (button) => this.work(
        `Starting ${engine.name}`, 'start_engine', {}, button),
      'look-again': () => this.look(),
    }).querySelector('[data-act="engine-primary"]').textContent = `Start ${engine.name}`;
  }

  /** An email and a password typed twice, checked as they are typed:
   *  each mistake said beside its field, the button offered only when
   *  there is none. `given(fields)` is called with what was typed. */
  credentials(form, given) {
    const fields = form.elements;
    const submit = form.querySelector('[type=submit]');
    const check = () => {
      const email = fields.email.value.trim();
      const password = fields.password.value;
      const again = fields.again.value;
      const rules = FirstPerson.rules(password);
      for (const item of form.querySelectorAll('[data-rule]')) {
        item.classList.toggle('is-met', rules[item.dataset.rule]);
      }
      const problems = {
        email: FirstPerson.emailProblem(email),
        again: FirstPerson.againProblem(password, again)
          || FirstPerson.passwordProblem(password),
      };
      for (const [name, problem] of Object.entries(problems)) {
        form.querySelector(`[data-for="${name}"]`).textContent = problem;
        fields[name].classList.toggle('is-wrong', Boolean(problem));
      }
      const named = !fields.name || Boolean(fields.name.value.trim());
      submit.disabled = !named || !FirstPerson.ready(email, password, again);
    };
    form.addEventListener('input', check);
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      if (!submit.disabled) given(fields);
    });
    check();
  }

  setup() {
    const view = this.show('setup');
    this.credentials(view.querySelector('form'), (fields) => {
      this.work('Installing DecentAI', 'install', {
        name: fields.name.value.trim(),
        email: fields.email.value.trim(),
        password: fields.password.value,
      }, null, 'Keep this window open. The download is the long part; the rest takes a minute.');
    });
  }

  reset() {
    const view = this.show('reset', {}, { back: () => this.settle() });
    const form = view.querySelector('form');
    form.elements.email.value = this.state.first_person || '';
    this.credentials(form, async (fields) => {
      const email = fields.email.value.trim();
      const done = await this.work('Setting the password', 'reset_password', {
        email, password: fields.password.value,
      }, null, '', true);
      if (done === undefined) return;
      this.show('reset-done', {
        text: `${email} signs in with the new password. Every device that was signed in to that account has been signed out.`,
      }, { back: () => this.settle() });
    });
    (form.elements.email.value ? form.elements.password : form.elements.email).focus();
  }

  /** Which DecentAI is installed — and which app this is, where the two
   *  differ: the app and DecentAI are released together and updated
   *  apart, and a number with no name beside it is read as DecentAI's. */
  installed() {
    const state = this.state;
    const app = state.app_version && state.app_version !== state.version
      ? ` · this app is ${state.app_version}` : '';
    return `DecentAI ${state.version} is installed${app}`;
  }

  running() {
    const state = this.state;
    this.show('running', {
      address: state.address,
      version: this.installed(),
    }, {
      open: () => this.backend.ask('open'),
      stop: (button) => this.work('Stopping DecentAI', 'stop', {}, button),
      update: (button) => this.work(
        `Updating to ${state.update.version}`, 'update', {}, button,
        'Chats that are working are interrupted. A copy of the database is kept first, '
        + 'and the version you have is put back if the new one does not start.'),
      check: () => this.look(),
      reset: () => this.reset(),
      data: () => this.data(),
      uninstall: () => this.uninstall(),
    });
    if (state.update) {
      this.view.querySelector('[data-slot="update"]').hidden = false;
      this.view.querySelector('[data-slot="update-title"]').textContent =
        `DecentAI ${state.update.version} is available`;
      this.view.querySelector('[data-slot="update-text"]').textContent =
        `You have ${state.version}. Updating takes about a minute.`;
    }
  }

  stopped() {
    this.show('stopped', { version: this.installed() }, {
      start: (button) => this.work('Starting DecentAI', 'start', {}, button),
      reset: () => this.reset(),
      data: () => this.data(),
      uninstall: () => this.uninstall(),
    });
  }

  async data() {
    let found;
    try {
      found = await this.backend.ask('data');
    } catch (failed) {
      return this.failed('That could not be read.', failed);
    }
    const view = this.show('data', { engine: found.engine }, { back: () => this.settle() });
    const list = view.querySelector('[data-slot="places"]');
    for (const place of found.places) {
      const item = document.createElement('li');
      item.textContent = place;
      list.append(item);
    }
  }

  uninstall() {
    const view = this.show('uninstall', {}, {
      back: () => this.settle(),
      'uninstall-now': async () => {
        const keep = view.querySelector('[name=keep]').checked;
        const images = view.querySelector('[name=images]').checked;
        const done = await this.work('Removing DecentAI', 'uninstall',
          { keep, images }, null, '', true);
        if (done === undefined) return;
        this.show('gone', {
          text: done && done.kept
            ? `A copy of the database was kept: ${done.kept}`
            : 'Nothing of it is left on this computer.',
        }, { 'look-again': () => this.look() });
      },
    });
  }

  // -- work ------------------------------------------------------------

  /** Run one long command, showing what it says as it goes. Returns
   *  what the command answered, or undefined when it failed (and the
   *  failure is on screen). `stay` leaves the next screen to the
   *  caller. */
  async work(title, command, payload, button, note, stay) {
    if (button) button.disabled = true;
    const view = this.show('working', { title, note: note || '' });
    this.steps = view.querySelector('[data-slot="steps"]');
    try {
      const answer = await this.backend.ask(command, payload);
      if (stay) { this.steps = null; return answer === undefined ? null : answer; }
      await this.look();
      return answer === undefined ? null : answer;
    } catch (failed) {
      this.failed(`${title} did not finish.`, failed);
      return undefined;
    }
  }

  /** One line of work under way: the one before it is done. */
  said(text) {
    if (!this.steps || !text.trim()) return;
    for (const done of this.steps.querySelectorAll('.is-now')) {
      done.classList.replace('is-now', 'is-done');
    }
    const item = document.createElement('li');
    item.className = 'is-now';
    item.textContent = text.trim();
    this.steps.append(item);
    item.scrollIntoView({ block: 'nearest' });
  }

  failed(what, why) {
    const said = String((why && why.message) || why || '').trim();
    const view = this.show('failed', { text: what }, { 'look-again': () => this.look() });
    if (said) {
      const place = view.querySelector('[data-slot="said"]');
      place.hidden = false;
      place.textContent = said;
    }
  }
}

window.addEventListener('DOMContentLoaded', () => {
  const backend = window.__TAURI__ ? new Backend(window.__TAURI__) : new PretendBackend();
  new App(backend).look();
});
