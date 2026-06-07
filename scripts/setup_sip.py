"""One-time setup: create the SIP dispatch rule for inbound phone calls.

Creates an individual dispatch rule that:
  - Places each inbound caller in a fresh room prefixed "call-"
  - Auto-dispatches the dispatch-orchestrator agent to that room

Run once from the repo root:
    make setup-sip

After this, assign your phone number to the printed dispatch rule ID:
    lk number update --number +1XXXXXXXXXX --sip-dispatch-rule-id <RULE_ID>

Or via LiveKit Cloud: Telephony → Phone Numbers → ⋮ → Assign dispatch rule.
"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv

load_dotenv(".env.local")
load_dotenv(".env")

from livekit import api  # noqa: E402

AGENT_NAME = "dispatch-orchestrator"
RULE_NAME = "dispatch-this-batch inbound"
ROOM_PREFIX = "call-"


async def main() -> None:
    lkapi = api.LiveKitAPI()

    rule = api.SIPDispatchRule(
        dispatch_rule_individual=api.SIPDispatchRuleIndividual(
            room_prefix=ROOM_PREFIX,
        )
    )

    request = api.CreateSIPDispatchRuleRequest(
        dispatch_rule=api.SIPDispatchRuleInfo(
            rule=rule,
            name=RULE_NAME,
            room_config=api.RoomConfiguration(
                agents=[
                    api.RoomAgentDispatch(
                        agent_name=AGENT_NAME,
                        metadata="",
                    )
                ]
            ),
        )
    )

    result = await lkapi.sip.create_sip_dispatch_rule(request)
    await lkapi.aclose()

    rule_id = result.sip_dispatch_rule_id
    print(f"Dispatch rule created: {rule_id}")
    print()
    print("Next: assign your phone number to this rule:")
    print(f"  lk number update --number +1XXXXXXXXXX --sip-dispatch-rule-id {rule_id}")
    print()
    print("Or via LiveKit Cloud: Telephony → Phone Numbers → ⋮ → Assign dispatch rule")


if __name__ == "__main__":
    asyncio.run(main())
