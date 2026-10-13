"""An agent's README, carried in the catalog so a review can show it."""

from api.services.agents.repository import MAX_README_CHARS, Repository


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
