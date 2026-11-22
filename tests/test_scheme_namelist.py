from backend.app.integrations.codex.scheme_namelist import namelist_excerpt


def test_namelist_distinguishes_active_and_commented_sections_without_values():
    value = """Sample input
&general
path="/tmp/private", token="unpublished"
/
&GB
igb=5, saltcon=0.150
&end
#&pb
#istrng=0.15
#/
! &decomp ! private description
! print_res="within 4"
! &END
"""
    assert namelist_excerpt(value, limit=1600).splitlines() == [
        'L2: Active namelist section &general',
        'L5: Active namelist section &gb',
        'L8: Commented-out namelist section &pb',
        'L11: Commented-out namelist section &decomp',
    ]


def test_namelist_requires_complete_active_sections_and_a_whole_line_budget():
    assert namelist_excerpt('include README.md\n', limit=1600) == ''
    assert namelist_excerpt('#&example\n#/\n', limit=1600) == ''
    assert namelist_excerpt('&general\nvalue=1\n', limit=1600) == ''
    assert namelist_excerpt('&general\n&nested\n/\n/\n', limit=1600) == ''
    assert namelist_excerpt('&general\n/\n#&unclosed\n', limit=1600) == 'L1: Active namelist section &general'
    assert namelist_excerpt('&general\n/\n', limit=12) == ''
