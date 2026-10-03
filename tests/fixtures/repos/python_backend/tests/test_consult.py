from app.routers.consult import consult


def test_an_empty_question_returns_no_answers() -> None:
    assert consult({"question": ""})["answers"] == []


def test_a_known_expression_returns_its_intent() -> None:
    result = consult({"question": "expression 1", "language": "ES"})
    assert result["language"] == "es"
    assert result["answers"][0]["intent"] == "payment_split_001"
