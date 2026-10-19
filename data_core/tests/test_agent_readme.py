"""An agent's README, carried in the catalog so a review can show it."""

from api.services.agents.repository import MAX_LISTED_FILES, MAX_README_CHARS, Repository


def test_the_readme_is_read_whatever_its_case(tmp_path):
    (tmp_path / "ReadMe.md").write_text("# Notebook\n\nKeeps notes.\n", encoding="utf-8")
    assert Repository._readme(tmp_path) == "# Notebook\n\nKeeps notes.\n"


def test_an_agent_without_one_has_none(tmp_path):
    (tmp_path / "manifest.yaml").write_text("agent: {}\n", encoding="utf-8")
    assert Repository._readme(tmp_path) == ""


def test_a_readme_too_long_to_review_is_cut_and_says_so(tmp_path):
    (tmp_path / "README.md").write_text("x" * (MAX_README_CHARS + 500), encoding="utf-8")
    read = Repository._readme(tmp_path)
    assert read.startswith("x" * MAX_README_CHARS)
    assert read.endswith("*The README continues in the repository.*")
    assert len(read) < MAX_README_CHARS + 100


def test_a_folder_named_like_one_is_not_read(tmp_path):
    (tmp_path / "README.md").mkdir()
    assert Repository._readme(tmp_path) == ""


def test_the_package_is_listed_file_by_file(tmp_path):
    """Names and sizes, in the order a tree shows them, and nothing a
    build leaves behind."""
    (tmp_path / "manifest.yaml").write_bytes(b"agent: {}\n")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "notes.py").write_bytes(b"x = 1\n")
    (tmp_path / "tools" / "__pycache__").mkdir()
    (tmp_path / "tools" / "__pycache__" / "notes.pyc").write_bytes(b"\0")
    listed = Repository._files(tmp_path)
    assert listed == {
        "files": [{"path": "manifest.yaml", "size": 10},
                  {"path": "tools/notes.py", "size": 6}],
        "files_more": 0,
    }


def test_a_package_of_many_files_says_how_many_more(tmp_path):
    for number in range(MAX_LISTED_FILES + 7):
        (tmp_path / f"f{number:04d}.txt").write_text("", encoding="utf-8")
    listed = Repository._files(tmp_path)
    assert len(listed["files"]) == MAX_LISTED_FILES
    assert listed["files_more"] == 7
