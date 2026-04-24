# 10. Role-Based Personalization

The advisor is used by operators, supervisors, process engineers, and
maintenance crews. Each role asks different questions, brings different
context, and benefits from different framings of the same evidence.
v3.0 ships **substrate-level personalization** — the schema, auth, and
prompt structure all carry a role concept; the per-role response shaping
is partial and documented as such here.

## 10.1 The Role Spine

Three columns flow per-user data through the system:

- `user_profiles.default_role` — `operator | supervisor | engineer |
  maintenance | analyst`
- `user_permissions.scope JSONB` — `{ "lines": ["coater1"], "shifts": ["A","B","C","D"], "view": [...] }`
- `messages.context_snapshot.user.role` — copied at request time so the
  audit record carries the role that drove a given response

The JWT issued by the Ignition gateway carries `sub` (user id), `role`,
and `scope` claims; `routers/deps.py::require_attributed_user` resolves
these to a `user_profiles` row. The TTL cache `_PERMISSIONS_CACHE`
(60 s) keeps per-request DB hits to amortized zero.

## 10.2 What's Wired Today (As-Built)

The following role-aware behaviors are live in the MVP:

- **Memory scope filter.** Approved memory entries with
  `equipment_scope`/`failure_mode_scope`/`style_scope` that don't
  intersect the user's permission scope are excluded from retrieval.
- **Audit attribution.** Every action in `audit_log.actor_user_id` is
  the resolved user, not a service identity. This is regulatory-grade
  attribution.
- **Rate-limit keying.** `services/routers/rate_limit.py::chat_user_key`
  uses the resolved `user_id` so per-user throttling is correct rather
  than per-IP (operators on shared HMI workstations would otherwise
  share a quota).
- **Role passed to prompt.** The system prompt template includes
  `<user_role>` in its USER block, and the response posture micro-tunes
  on it: `engineer` gets full evidence detail; `operator` gets terse
  action-first framing; `supervisor` gets summary + Pareto framing.

## 10.3 What's Stubbed (Documented as Such)

Several v2.0-promised personalization paths are deferred:

- **Density preference.** `user_profiles.personalization.density` is
  read but not yet acted on. Will gate paragraph length and bullet
  density when implemented.
- **Preferred-style examples.** "Show me examples like X" requires
  a per-user examples table; not yet provisioned.
- **Per-role default tools.** All roles see the same five tools today;
  long-term `analyst` may get a SQL-builder tool, `maintenance` a
  WO-history-summarizer.

## 10.4 Why Personalization Is Not the Frontier

This is a deliberate prioritization. Personalization is a **second-order
quality-of-life** feature. The trust + grounding substrate (chapters 4,
6, 7, 8, 9) is the **first-order** correctness substrate. An advisor
that gives the wrong answer beautifully is worse than one that gives
the right answer plainly.

The role spine is in place so personalization can be added in slices
without re-plumbing. That's the bar v3.0 commits to; richer per-role
shaping is a Phase 4 milestone.

<div class="delta-box">
<p class="delta-title">Δ vs v2.0 — Personalization</p>
<p><span class="label">Stayed:</span> The five-role taxonomy. Audit
attribution. Permission scope as the access-control substrate.</p>
<p><span class="label">Changed:</span> Role passed through to prompt;
memory scope filter applied at retrieval time. Rate-limit keying
moved from IP to user_id (correct for shared HMIs).</p>
<p><span class="label">Considering:</span> Density preference. Per-role
tool subsets. Per-user "interest" weighting on tag categories.
Saved-question library with role-shared and role-private scopes.</p>
</div>
