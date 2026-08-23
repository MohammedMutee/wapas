"""Adversarial suite: twenty ways to make Wapas do something it must not.

Unit tests check that each rule works. This checks something different and
harder to fake — that an *attacker* who knows the design cannot get a forbidden
action executed, including through paths no single unit test covers: a
misdiagnosis that unlocks the wrong playbook, free text from a counterparty
reaching a model, a tampered audit chain, a live key in the environment.

Every scenario states what it is trying to achieve, what must happen instead,
and what an escape would mean. The suite reports **escapes**, and any escape
above zero is a failed build.
"""
