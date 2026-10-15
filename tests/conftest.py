import pytest

from helpers import default_rows, make_xlsx


@pytest.fixture
def xlsx(tmp_path):
    return make_xlsx(tmp_path / "tc.xlsx", default_rows())
