"""A chain of manifests followed by its links, shared by the log archive (P1-T9c) and the nightly
dumps (P1-T22a, one chain per hospital). Moved here from deploy/logs/archive/archive.py.

Each manifest names the SHA-256 of the one before it (`previous`, None for the first). The chain
must have one root, no forks and no gaps; a manifest erased by the owner's expiry leaves an
authenticated erasure record naming it and its predecessor, through which the links are followed.
"""

import collections


def resolve(prev, present, erased):
    """Follows a link through erased manifests to the first one still present (or None)."""
    seen = set()
    while prev is not None and prev not in present and prev in erased and prev not in seen:
        seen.add(prev)
        prev = erased[prev]
    return prev


def follow(present, erased, created, label):
    """present: {sha256: manifest}; erased: {sha256 of an erased manifest: its previous};
    created(sha) orders siblings; label(sha) names a manifest in problems.
    Returns (order, head, problems): every present manifest from each root along its links
    (a forked chain: every branch, oldest first), the newest manifest nothing follows, and the
    chain's problems as text."""
    children = collections.defaultdict(list)
    roots, problems, order = [], [], []
    for sha, m in present.items():
        p = resolve(m["previous"], present, erased)
        if p is None:
            roots.append(sha)
        elif p in present:
            children[p].append(sha)
        else:
            problems.append(f"{label(sha)}: the manifest before it is missing (deleted without an erasure record?)")
    for parent, kids in children.items():
        if len(kids) > 1:
            problems.append(f"{label(parent)}: {len(kids)} manifests follow it (the chain forks)")
    if len(roots) > 1:
        problems.append(f"{len(roots)} manifests start a chain (only the first may)")
    stack = sorted(roots, key=created, reverse=True)
    while stack:
        sha = stack.pop()
        order.append(sha)
        stack.extend(sorted(children.get(sha, []), key=created, reverse=True))
    heads = [sha for sha in present if not children.get(sha)]
    head = max(heads, key=created) if heads else None
    return order, head, problems
