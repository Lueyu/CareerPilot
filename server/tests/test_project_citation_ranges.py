"""Citations may span contiguous supplied chunks, never unread gaps or versions."""
import pytest

from app.services.project_autopilot_engine import StageError, _validate_refs

REF = {'source_id': 'same-source', 'start_line': 183, 'end_line': 187}


def chunk(start, end, **changes):
    return {'source_id': 'same-source', 'path': 'docs/plan.md', 'source_version': 'a' * 40,
            'start_line': start, 'end_line': end, **changes}


@pytest.mark.parametrize('chunks', [
    [chunk(1, 185), chunk(186, 187)],
    [chunk(186, 187), chunk(1, 185)],
    [chunk(1, 185), chunk(184, 186), chunk(187, 190)],
])
def test_reference_across_contiguous_read_chunks_retains_exact_provenance(chunks):
    assert _validate_refs([REF], chunks) == [
        {**REF, 'path': 'docs/plan.md', 'source_version': 'a' * 40}]


@pytest.mark.parametrize('chunks', [
    [chunk(1, 185), chunk(187, 190)],  # line 186 was never supplied
    [chunk(1, 185), chunk(186, 187, source_version='b' * 40)],
    [chunk(1, 185), chunk(186, 187, path='docs/other.md')],
    [chunk(1, 185), chunk(186, 187, source_id='other-source')],
    [chunk(184, 190)],  # first cited line was never supplied
])
def test_reference_cannot_bridge_gaps_or_source_identities(chunks):
    with pytest.raises(StageError) as error:
        _validate_refs([REF], chunks)
    assert error.value.category == 'INVALID_CITATION'


def test_single_chunk_reference_stays_compatible():
    assert _validate_refs([REF], [chunk(1, 200)]) == [
        {**REF, 'path': 'docs/plan.md', 'source_version': 'a' * 40}]


def test_reversed_reference_cannot_claim_an_unread_range():
    with pytest.raises(StageError) as error:
        _validate_refs([{**REF, 'start_line': 999, 'end_line': 1}], [chunk(1, 5)])
    assert error.value.category == 'INVALID_CITATION'
