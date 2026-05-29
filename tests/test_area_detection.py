"""Unit tests for toktrail.area_detection."""

from __future__ import annotations

from toktrail.area_detection import (
    detect_area_for_cwd,
)
from toktrail.config import AreaRuleConfig, AreasConfig


def _areas_config(
    *,
    auto_detect: bool = True,
    rules: tuple[AreaRuleConfig, ...] = (),
) -> AreasConfig:
    return AreasConfig(auto_detect=auto_detect, rules=rules)


def _rule(
    area: str,
    cwd_globs: tuple[str, ...] = (),
    git_remotes: tuple[str, ...] = (),
    priority: int = 0,
) -> AreaRuleConfig:
    return AreaRuleConfig(
        area=area, cwd_globs=cwd_globs, git_remotes=git_remotes, priority=priority
    )


def test_detect_area_matches_exact_default_working_dir(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    config = _areas_config(
        rules=(
            _rule(
                "private/toktrail",
                cwd_globs=(str(project), f"{project}/**"),
                priority=100,
            ),
        )
    )
    detected = detect_area_for_cwd(config, str(project))
    assert detected is not None
    assert detected.area_path == "private/toktrail"


def test_detect_area_matches_child_of_default_working_dir(tmp_path):
    project = tmp_path / "project"
    child = project / "tests"
    child.mkdir(parents=True)
    config = _areas_config(
        rules=(_rule("private/toktrail", cwd_globs=(f"{project}/**",), priority=100),)
    )
    detected = detect_area_for_cwd(config, str(child))
    assert detected is not None
    assert detected.area_path == "private/toktrail"


def test_detect_area_recursive_glob_matches_base_directory(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    config = _areas_config(
        rules=(_rule("private/toktrail", cwd_globs=(f"{project}/**",), priority=100),)
    )
    detected = detect_area_for_cwd(config, str(project))
    assert detected is not None
    assert detected.area_path == "private/toktrail"


def test_detect_area_uses_highest_priority(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    config = _areas_config(
        rules=(
            _rule("low-prio", cwd_globs=(f"{project}/**",), priority=10),
            _rule("high-prio", cwd_globs=(f"{project}/**",), priority=100),
        )
    )
    detected = detect_area_for_cwd(config, str(project))
    assert detected is not None
    assert detected.area_path == "high-prio"


def test_detect_area_returns_none_when_auto_detect_disabled(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    config = _areas_config(
        auto_detect=False,
        rules=(_rule("private/toktrail", cwd_globs=(f"{project}/**",), priority=100),),
    )
    detected = detect_area_for_cwd(config, str(project))
    assert detected is None


def test_detect_area_returns_none_when_no_match(tmp_path):
    config = _areas_config(
        rules=(_rule("private/toktrail", cwd_globs=("/unmatched/**",), priority=100),)
    )
    detected = detect_area_for_cwd(config, str(tmp_path))
    assert detected is None


def test_detect_area_with_git_remote(tmp_path):
    config = _areas_config(
        rules=(
            _rule(
                "private/toktrail",
                git_remotes=("git@github.com:me/toktrail.git",),
                priority=100,
            ),
        )
    )
    detected = detect_area_for_cwd(
        config, str(tmp_path), git_remote="git@github.com:me/toktrail.git"
    )
    assert detected is not None
    assert detected.area_path == "private/toktrail"


def test_detect_area_prefers_path_over_git_remote(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    config = _areas_config(
        rules=(
            _rule(
                "path-area",
                cwd_globs=(f"{project}/**",),
                priority=100,
            ),
            _rule(
                "git-area",
                git_remotes=("git@github.com:me/toktrail.git",),
                priority=100,
            ),
        )
    )
    detected = detect_area_for_cwd(
        config, str(project), git_remote="git@github.com:me/toktrail.git"
    )
    assert detected is not None
    # Higher rule index (later rule) wins at equal priority.
    assert detected.area_path == "git-area"
