from astrbot_plugin_kouhu_judge.statement_layout import quick_statement


def test_flavor_removed_goal_moved_without_losing_rules():
    rule='A sequence contains black and white stripes. Split it and reverse both parts.'
    goal='Determine the maximum possible length of the zebra.'
    target,body=quick_statement('Is there anything better than going to the zoo after a tiresome week at work?\n\n'+rule+'\n\n'+goal)
    assert target==goal and body==rule


def test_story_with_rule_or_number_is_preserved():
    for prefix in ['Once upon a time Alice had 3 coins.','One sunny day players can make a move.']:
        objective,body=quick_statement(prefix+'\n\nFind the maximum score.')
        assert prefix in body and objective=='Find the maximum score.'


def test_no_clear_goal_preserves_all_formal_content():
    text='Given $n$ and $a_i$.\n\nThe operation replaces adjacent elements.'
    objective,body=quick_statement(text)
    assert objective=='' and body==text


def test_ordinary_intro_not_guessed_as_useless():
    text='Alice and Bob begin a competition.\n\nThere are tokens on a line.'
    assert quick_statement(text)==('',text)
