"""Explicit one-attempt real call from a trusted deployment terminal."""

import argparse
import asyncio
import json
import secrets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan-id', type=int, required=True)
    parser.add_argument('--phone', required=True)
    parser.add_argument('--approve-real-call', action='store_true', required=True)
    parser.add_argument('--branch-id', default='agtbrch_7701m5q3y1cwf8bt2qbeeg78tf6t')
    args = parser.parse_args()

    from fastapi import HTTPException

    from app.config import settings
    from app.database import SessionLocal
    from app.followups import FollowUpPlan, ImmediateCallIn, call_now

    # This process alone opts into real telephony. Recurring scheduler configuration
    # and the running API's mock setting are not changed.
    settings.mock_telephony = False
    settings.eleven_immediate_call_branch_id = args.branch_id
    if not settings.eleven_configured:
        parser.error('ElevenLabs credentials are missing; no call was placed')
    if not settings.eleven_immediate_call_branch_id:
        parser.error('Set ELEVEN_IMMEDIATE_CALL_BRANCH_ID before calling')

    with SessionLocal() as db:
        plan = db.get(FollowUpPlan, args.plan_id)
        if plan is None:
            parser.error('Plan not found')
        approval = ImmediateCallIn(
            approved=True,
            revision=plan.revision,
            expected_phone=args.phone,
            request_key=secrets.token_hex(24),
        )
        try:
            result = asyncio.run(call_now(plan.id, approval, db))
        except HTTPException as exc:
            print(json.dumps({'status': 'not_confirmed', 'detail': exc.detail}))
            return 1
    print(json.dumps(result['call_dispatch']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
