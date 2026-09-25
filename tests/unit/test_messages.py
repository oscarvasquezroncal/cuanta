from __future__ import annotations

from cuanta.domain.messages import (
    ENGLISH,
    english,
    msg,
    option_message,
    placeholders,
    question_message,
    render,
)


def test_english_renders_params_and_falls_back_to_the_key() -> None:
    assert english(msg("stage.port", port=47300)) == "port 47300"
    assert english(msg("no.such.key")) == "no.such.key"


def test_nested_messages_render_inside_their_parent() -> None:
    evidence = msg("terminal.host", host="WindowsTerminal.exe")
    parent = msg("doctor.terminal.modern", evidence=evidence)
    assert english(parent) == "modern terminal (started from WindowsTerminal.exe)"


def test_render_leaves_unknown_placeholders_visible() -> None:
    assert render("{known} and {unknown}", {"known": "a"}) == "a and {unknown}"


def test_stored_questions_parse_back_to_their_message() -> None:
    triage = english(msg("question.triage", signature="ab12"))
    assert question_message(triage) == msg("question.triage", signature="ab12")
    assert question_message("something a user typed") is None


def test_options_map_to_keys_only_when_known() -> None:
    assert option_message("caused by this run") == msg("option.caused_by_this_run")
    assert option_message("pre-existing") == msg("option.pre_existing")
    assert option_message("banana") == "banana"


def test_every_english_template_has_well_formed_placeholders() -> None:
    for key, template in ENGLISH.items():
        assert template.count("{") == template.count("}"), key
        assert all(name.isidentifier() for name in placeholders(template)), key
