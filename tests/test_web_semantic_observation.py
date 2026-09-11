# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.


def test_semantic_tree_has_hierarchy_and_private_refs_never_escape():
    from fsq_agent.drivers.web._semantic import parse_semantics

    tree, candidates = parse_semantics("""- main [ref=e2]:
  - heading "Catalog" [level=1] [ref=e3]
  - combobox [ref=e4]:
    - option "Ten" [selected]
    - option "Twenty"
  - list "Results" [ref=e5]:
    - listitem [ref=e6]:
      - button "Add" [ref=e7]
    - listitem [ref=e8]:
      - button "Add" [ref=e9]
  - iframe [ref=e10]:
    - button "Inside" [ref=f1e2]
""")
    assert tree[0]["role"] == "main"
    assert tree[0]["children"][1]["role"] == "combobox"
    assert tree[0]["children"][1]["name"] == ""
    assert tree[0]["children"][1]["children"][0]["state"]["selected"] is True
    assert "ref=" not in str(tree)
    assert "f1e2" not in str(tree)
    assert next(item for item in candidates if item["name"] == "Inside")["native_ref"] == "f1e2"
    assert next(item for item in candidates if item["name"] == "Add")["parent"]["role"] == "listitem"


def test_semantic_name_with_ellipsis_stays_exact_and_long_content_is_preserved():
    from fsq_agent.drivers.web._semantic import parse_semantics

    text = "A" * 2000 + "..."
    tree, candidates = parse_semantics(f'- button "{text}" [ref=e2]')
    assert tree[0]["name"] == text
    assert candidates[0]["name"] == text
