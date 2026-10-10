from mfdataindia.api import association_tags


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _RecordingConnection:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def execute(self, query, params):
        self.calls.append((query, params))
        return _Result(self.row)


def test_get_state_reads_version_and_tags_in_one_statement() -> None:
    family_id = "11111111-1111-1111-1111-111111111111"
    tags = [
        {
            "value": "nippon-india-taiwan-equity-fund",
            "type": "scheme_alias",
            "source": "trillion-insights",
        }
    ]
    conn = _RecordingConnection(
        {"tlws_mf_id": family_id, "version": 4, "tags": tags}
    )

    state = association_tags.get_state(conn, family_id)

    assert state == {"tlws_mf_id": family_id, "version": 4, "tags": tags}
    assert len(conn.calls) == 1
    query, params = conn.calls[0]
    assert "jsonb_agg" in query
    assert "fund_family_association_state" in query
    assert "fund_family_association_tags" in query
    assert params == (family_id,)


def test_get_state_missing_family_uses_the_same_single_statement() -> None:
    family_id = "22222222-2222-2222-2222-222222222222"
    conn = _RecordingConnection(None)

    try:
        association_tags.get_state(conn, family_id)
    except association_tags.FamilyNotFoundError as exc:
        assert exc.args == (family_id,)
    else:
        raise AssertionError("missing family should raise FamilyNotFoundError")

    assert len(conn.calls) == 1
