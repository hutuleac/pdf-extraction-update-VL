import pytest

from extractor.errors import ResourceLimitError
from extractor.limits import MAX_TABLE_CELLS
from extractor.model import make_table_block


def test_table_cell_limit_refuses_oversized_table():
    rows = [["x"] * (MAX_TABLE_CELLS + 1)]
    with pytest.raises(ResourceLimitError):
        make_table_block(rows)
