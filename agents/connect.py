"""Phase 3: `connect_party(role, mode)` — the SIP-portability seam.

Same room, audio path, and transcript capture; only how the callee joins differs:
  sim -> AgentDispatchService.createDispatch(agency AI)
  sip -> ctx.api.sip.create_sip_participant(trunk, number, room)  (Twilio stretch)
TODO(Phase 3).
"""
