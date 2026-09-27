from __future__ import annotations

from pathlib import Path

import pytest

from cuanta.application.map import MapQuery
from cuanta.bootstrap import Container


def test_map_search_file_and_status_use_the_same_refreshed_index(tmp_path: Path) -> None:
    (tmp_path / "cart.py").write_text("def checkout():\n    return 1\n", encoding="utf-8")
    (tmp_path / "page.py").write_text("from cart import checkout\n", encoding="utf-8")
    container = Container.for_project(tmp_path)
    query = MapQuery(container.index_reader())
    try:
        state = query.status()
        assert state.status.files == 2 and state.status.coverage == 1
        assert state.status.updated_at and not state.semantic and state.stale_facts == 0
        assert query.search("checkout")[0].path == "cart.py"
        query.reader.service.note("cart.py", "Checkout handles totals", 1, 2)
        details = query.file("cart.py")
        assert details.card.estimated_tokens <= 120
        assert len(details.facts) == 1 and not details.stale
        assert any(row.path == "page.py" and row.target == "cart.py" for row in details.impact)
        (tmp_path / "cart.py").write_text("def checkout():\n    return 2\n", encoding="utf-8")
        stale = query.file("cart.py")
        assert not stale.facts and len(stale.stale) == 1
        assert "Checkout handles totals" not in stale.card.text
        state = query.revalidate()
        assert state.stale_facts == 1
        assert query.file("cart.py").stale == stale.stale
    finally:
        query.close()
        container.close()


def test_map_revalidate_recovers_only_matching_original_anchor(tmp_path: Path) -> None:
    path = tmp_path / "cart.py"
    text = "total = 1\nother = 2\n"
    path.write_text(text, encoding="utf-8")
    container = Container.for_project(tmp_path)
    query = MapQuery(container.index_reader())
    try:
        query.status()
        query.reader.service.note("cart.py", "Total source", 1)
        path.write_text("total = 3\nother = 2\n", encoding="utf-8")
        assert query.revalidate().stale_facts == 1
        path.write_text(text, encoding="utf-8")
        assert query.revalidate().stale_facts == 0
        assert len(query.file("cart.py").facts) == 1
        with pytest.raises(ValueError, match="inside the project"):
            query.file("../cart.py")
        with pytest.raises(ValueError, match="not indexed"):
            query.file("absent.py")
    finally:
        query.close()
        container.close()
