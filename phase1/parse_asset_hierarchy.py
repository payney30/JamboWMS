"""
parse_asset_hierarchy.py

Derives the two location-classification lookups used by the dashboard pipeline
directly from Asset_Hierarchy_Analysis.md (the authoritative source of truth
for the asset/location hierarchy, maintained in project knowledge).

Run this first on any day the hierarchy doc has changed; otherwise the
existing name_to_branch.json / name_to_camp_letter.json in /home/claude/build/
can be reused as-is.

Outputs:
  - name_to_branch.json        asset name -> dashboard location group
                                (one of the 9 hierarchy branches, or
                                "Jamboree-wide" / "Unassigned")
  - name_to_camp_letter.json   asset name -> physical base camp letter
                                (only for assets under NJ Base Camps Ops)
  - location_hierarchy.json    full nested tree (name, code, branch_label,
                                children[]) -- powers the hierarchical
                                location picker in the requester form and
                                LOC triage screen. Unlike the two flat
                                lookups above, this preserves every
                                intermediate level (camp -> subcamp ->
                                shower house, etc.), not just the leaf's
                                top-level branch.

Standing overrides applied here (update if operational assignments change):
  - Base Camp A and Base Camp B (incl. their subcamps/shower houses and
    ServMart A/B) are reassigned from "Base Camps" to "Program Areas",
    since both camps are being used for Program purposes this Jamboree.
"""
import re
import json
import sys

MD_PATH = '/mnt/project/Asset_Hierarchy_Analysis.md'
OUT_BRANCH = '/home/claude/build/name_to_branch.json'
OUT_LETTER = '/home/claude/build/name_to_camp_letter.json'
OUT_TREE = '/home/claude/build/location_hierarchy.json'

BRANCH_LABELS = {
    'NJ Base Camps Ops': 'Base Camps',
    'NJ Program': 'Program Areas',
    'NJ Administration': 'Administration',
    'NJ General/Other': 'General/Other',
    'NJ Off-Site Properties': 'Off-Site Properties',
    'NJ Medical': 'Medical',
    'NJ Food': 'Food',
    'NJ Logistics': 'Logistics',
    'Summit Housing & Conference Centers': 'Summit Housing & Conference Centers',
}

# Camps currently reassigned to Program Areas for dashboard purposes.
# (Physically still Base Camps Ops in the hierarchy -- this is a standing
# operational override, not a hierarchy correction.)
PROGRAM_ONLY_CAMP_LETTERS = ['A', 'B']


class Node:
    __slots__ = ('name', 'code', 'depth', 'children', 'parent')

    def __init__(self, name, code, depth):
        self.name = name
        self.code = code
        self.depth = depth
        self.children = []
        self.parent = None


def parse_md_tree(md_path):
    lines = open(md_path).read().split('\n')
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith('## 5. Full hierarchy tree'))
    except StopIteration:
        sys.exit(f"Could not find '## 5. Full hierarchy tree' section in {md_path}")
    body = lines[start:]

    branch_re = re.compile(r'^### (.+?) \(`(.+?)`\)\s*$')
    item_re = re.compile(r'^(\s*)- \*\*(.+?)\*\* `(.*?)`')

    branches = []  # (branch_name, branch_code, root_node)
    cur_stack = None
    pending = None
    for l in body:
        bm = branch_re.match(l)
        if bm:
            pending = (bm.group(1), bm.group(2))
            cur_stack = 'pending'
            continue
        im = item_re.match(l)
        if im and cur_stack is not None:
            indent_str, name, code = im.groups()
            depth = len(indent_str) // 2
            if cur_stack == 'pending':
                root = Node(name, code, 0)
                branches.append((pending[0], pending[1], root))
                cur_stack = [(0, root)]
                continue
            while cur_stack and cur_stack[-1][0] >= depth:
                cur_stack.pop()
            parent = cur_stack[-1][1]
            node = Node(name, code, depth)
            node.parent = parent
            parent.children.append(node)
            cur_stack.append((depth, node))
    return branches


def find_node(root, name):
    if root.name == name:
        return root
    for c in root.children:
        r = find_node(c, name)
        if r:
            return r
    return None


def collect_names(node, acc):
    acc.append(node.name)
    for c in node.children:
        collect_names(c, acc)


def build_name_to_branch(branches):
    name_to_branch = {}

    def walk(n, label):
        name_to_branch[n.name] = label
        for c in n.children:
            walk(c, label)

    for name, code, root in branches:
        walk(root, BRANCH_LABELS.get(name, name))

    # Root wrapper nodes aren't part of any branch subtree in the doc's tree
    # section -- work orders logged directly against these fall to "Jamboree-wide".
    name_to_branch['National Jamboree - SBR'] = 'Jamboree-wide'
    name_to_branch['Jamboree 2026'] = 'Jamboree-wide'

    # Standing override: Base Camp A & B -> Program Areas
    bc_root = next(root for name, code, root in branches if name == 'NJ Base Camps Ops')
    for letter in PROGRAM_ONLY_CAMP_LETTERS:
        camp = find_node(bc_root, f'NJ Base Camp {letter}')
        if not camp:
            continue
        names = []
        collect_names(camp, names)
        sm = find_node(bc_root, f'NJ ServMart {letter}')
        if sm:
            names.append(sm.name)
        for nm in names:
            name_to_branch[nm] = 'Program Areas'

    return name_to_branch


def build_name_to_camp_letter(branches):
    bc_root = next(root for name, code, root in branches if name == 'NJ Base Camps Ops')
    name_to_letter = {}
    for child in bc_root.children:
        letter = None
        if child.name.startswith('NJ Base Camp '):
            letter = child.name.replace('NJ Base Camp ', '').strip()
        elif child.name.startswith('NJ ServMart '):
            letter = child.name.replace('NJ ServMart ', '').strip()
        if letter and len(letter) == 1:
            def collect(n, letter=letter):
                name_to_letter[n.name] = letter
                for c in n.children:
                    collect(c)
            collect(child)
    return name_to_letter


def node_to_dict(node, branch_label):
    """Serialize a Node (and its subtree) into the nested JSON shape used
    by the hierarchical location picker. Every node keeps its own
    branch_label so the frontend can group/filter without a second lookup."""
    return {
        'name': node.name,
        'code': node.code,
        'branch_label': branch_label,
        'children': [node_to_dict(c, branch_label) for c in node.children],
    }


def build_location_tree(branches):
    """Full nested tree, one root object per top-level branch, in the same
    order as BRANCH_LABELS / the source doc. This is the source of truth
    for the location picker UI -- it's what an admin-managed asset
    hierarchy (add/delete locations) would read from and write back to
    once this moves from a flat-file build step to a live DB table.

    Applies the same PROGRAM_ONLY_CAMP_LETTERS override as
    build_name_to_branch: Base Camp A/B (and their ServMarts/subcamps/
    shower houses) get branch_label='Program Areas' rather than
    'Base Camps', even though they're still physically nested under
    NJ Base Camps Ops in the tree structure itself. Only branch_label
    changes -- parent/child relationships are untouched, so the picker
    still shows them nested under Base Camps Ops; only the
    dashboard/KPI grouping differs, exactly as it does today via the
    flat lookup.
    """
    bc_root = next((root for name, code, root in branches if name == 'NJ Base Camps Ops'), None)
    override_names = set()
    if bc_root:
        for letter in PROGRAM_ONLY_CAMP_LETTERS:
            camp = find_node(bc_root, f'NJ Base Camp {letter}')
            if camp:
                collect_names(camp, list_ref := [])
                override_names.update(list_ref)
            sm = find_node(bc_root, f'NJ ServMart {letter}')
            if sm:
                override_names.add(sm.name)

    def node_to_dict_with_override(node, default_label):
        label = 'Program Areas' if node.name in override_names else default_label
        return {
            'name': node.name,
            'code': node.code,
            'branch_label': label,
            'children': [node_to_dict_with_override(c, default_label) for c in node.children],
        }

    tree = []
    for name, code, root in branches:
        label = BRANCH_LABELS.get(name, name)
        tree.append(node_to_dict_with_override(root, label))
    return tree


if __name__ == '__main__':
    branches = parse_md_tree(MD_PATH)
    name_to_branch = build_name_to_branch(branches)
    name_to_letter = build_name_to_camp_letter(branches)
    location_tree = build_location_tree(branches)

    json.dump(name_to_branch, open(OUT_BRANCH, 'w'), indent=1)
    json.dump(name_to_letter, open(OUT_LETTER, 'w'), indent=1)
    json.dump(location_tree, open(OUT_TREE, 'w'), indent=1)

    print(f"Parsed {len(branches)} top-level branches from {MD_PATH}")
    print(f"  name_to_branch: {len(name_to_branch)} entries -> {OUT_BRANCH}")
    print(f"  name_to_camp_letter: {len(name_to_letter)} entries -> {OUT_LETTER}")
    print(f"  location_hierarchy: {len(location_tree)} root branches -> {OUT_TREE}")
