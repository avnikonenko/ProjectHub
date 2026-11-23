from backend.app.integrations.codex.scheme_mdp import mdp_excerpt


def test_mdp_excerpt_keeps_allowlisted_template_settings_and_original_line_numbers():
    value = """; Equilibration data
integrator = md ; leap-frog
dt = 0.002
nsteps = 500000
TCoupl = V-rescale
pcoupl = Parrinello-Rahman
pcoupltype = isotropic
constraints = h-bonds
ref_t = 300    300
ref-p = 1e0
; dt = 0.004
include = /private/file
"""
    assert mdp_excerpt(value, limit=900).splitlines() == [
        'L2: MDP template integrator = md',
        'L3: MDP template dt = 0.002',
        'L4: MDP template nsteps = 500000',
        'L5: MDP template tcoupl = V-rescale',
        'L6: MDP template pcoupl = Parrinello-Rahman',
        'L7: MDP template pcoupltype = isotropic',
        'L8: MDP template constraints = h-bonds',
        'L9: MDP template ref-t = 300 300',
        'L10: MDP template ref-p = 1e0',
    ]


def test_mdp_excerpt_marks_repeated_settings_and_omits_unrecognized_data():
    value = """dt = .002
dt = 0.004
ref_t = 300
ref-t = unknown path
constraints = /private/file
ref-p = nan
nsteps = 1.5
gen_seed = 6071161
"""
    assert mdp_excerpt(value, limit=900).splitlines() == [
        'L1: MDP template dt = .002 (repeated setting in template)',
        'L2: MDP template dt = 0.004 (repeated setting in template)',
        'L3: MDP template ref-t = 300 (repeated setting in template)',
    ]


def test_mdp_excerpt_respects_whole_line_budget():
    value = 'tcoupl = no\npcoupl = no'
    first = 'L1: MDP template tcoupl = no'
    assert mdp_excerpt(value, limit=len(first) - 1) == ''
    assert mdp_excerpt(value, limit=len(first)) == first
