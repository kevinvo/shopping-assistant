"""Judge JSON parsing tolerates Markdown code fences.

Despite json_mode=True, DeepSeek sometimes wraps the judge object in a
```json ... ``` block. A bare json.loads() then raised and the caller silently
fell back to a 0.5 score, corrupting faithfulness/actionability/retrieval
metrics. These tests pin the fence-tolerant parsing and that the judges return
the real score instead of the 0.5 fallback.
"""

import json
from unittest.mock import patch

import pytest

from chalicelib.jobs import evaluator as ev


# --------------------------------------------------------------------------- #
# _loads_judge_json
# --------------------------------------------------------------------------- #
def test_parses_plain_json():
    assert ev._loads_judge_json('{"actionability": 0.9}') == {"actionability": 0.9}


def test_parses_json_fenced():
    raw = '```json\n{"actionability": 0.9, "specific_products_count": 3}\n```'
    assert ev._loads_judge_json(raw) == {
        "actionability": 0.9,
        "specific_products_count": 3,
    }


def test_parses_bare_fence():
    assert ev._loads_judge_json('```\n{"faithfulness": 0.8}\n```') == {
        "faithfulness": 0.8
    }


def test_parses_with_surrounding_whitespace():
    raw = '   ```json\n{"avg_relevance": 0.5}\n```   '
    assert ev._loads_judge_json(raw) == {"avg_relevance": 0.5}


def test_raises_on_garbage():
    with pytest.raises(json.JSONDecodeError):
        ev._loads_judge_json("not json at all")


# --------------------------------------------------------------------------- #
# Judge functions return the real score (not the 0.5 fallback) on fenced output
# --------------------------------------------------------------------------- #
@patch.object(ev, "get_actionability_user")
@patch.object(ev, "get_actionability_system")
@patch.object(ev, "judge_llm")
def test_actionability_parses_fenced_response(mock_llm, mock_sys, mock_user):
    mock_sys.return_value = ("system", None)
    mock_user.return_value = ("q={query} r={response}", None)
    mock_llm.chat.return_value = (
        '```json\n{"actionability": 0.95, "specific_products_count": 4,'
        ' "reasoning": "specific"}\n```'
    )

    result = ev.evaluate_actionability_llm("best knife", "Get the Victorinox.")

    assert result.actionability == 0.95
    assert result.specific_products_count == 4


@patch.object(ev, "get_faithfulness_user")
@patch.object(ev, "get_faithfulness_system")
@patch.object(ev, "judge_llm")
def test_faithfulness_parses_fenced_response(mock_llm, mock_sys, mock_user):
    mock_sys.return_value = ("system", None)
    mock_user.return_value = ("q={query} c={context} r={response}", None)
    mock_llm.chat.return_value = (
        '```json\n{"faithfulness": 0.88, "grounded": true, "reasoning": "ok"}\n```'
    )

    result = ev.evaluate_faithfulness("q", "context", "response")

    assert result.faithfulness == 0.88
    assert result.grounded is True
