"""Build-time smoke check for the runner/oracle privilege boundary."""
from __future__ import annotations

import os
import shlex

from benchmark.agent_environment import run_as_agent
from benchmark.env_setup import find_pred_binary
from benchmark.evidence_budget import EvidenceBudget, EvidenceBudgetSession
from benchmark.verify import Verdict


def main() -> None:
    uid = int(os.environ["PRB_AGENT_UID"])
    gid = int(os.environ["PRB_AGENT_GID"])
    oracle_uid = int(os.environ["PRB_ORACLE_UID"])
    oracle_gid = int(os.environ["PRB_ORACLE_GID"])
    evidence_gid = int(os.environ["PRB_EVIDENCE_GID"])
    pred_binary = find_pred_binary()
    budget = EvidenceBudget(
        model_generations=1,
        shell_actions=2,
        pred_calls=1,
        solve_calls=0,
        submit_attempts=2,
    )
    with EvidenceBudgetSession(
        rule="privilege-smoke",
        budget=budget,
        pred_binary=pred_binary,
        verifier=lambda cert: Verdict(False, "not used"),
        agent_uid=uid,
        agent_gid=gid,
        oracle_uid=oracle_uid,
        oracle_gid=oracle_gid,
        evidence_gid=evidence_gid,
    ) as session:
        env = dict(os.environ)
        through_gateway = run_as_agent(
            "pred --version",
            cwd=str(session.workdir),
            env=env,
            timeout=10,
            uid=uid,
            gid=gid,
            extra_groups=(evidence_gid,),
        )
        if through_gateway.returncode != 0:
            raise SystemExit(f"pred gateway failed for agent: {through_gateway.stdout}")

        direct = run_as_agent(
            f"{shlex.quote(str(pred_binary))} --version",
            cwd=str(session.workdir),
            env=env,
            timeout=10,
            uid=uid,
            gid=gid,
            extra_groups=(evidence_gid,),
        )
        if direct.returncode == 0:
            raise SystemExit("agent unexpectedly executed the real pred oracle")

        submit_status = run_as_agent(
            "submit --status",
            cwd=str(session.workdir),
            env=env,
            timeout=10,
            uid=uid,
            gid=gid,
            extra_groups=(evidence_gid,),
        )
        if submit_status.returncode != 0:
            raise SystemExit(f"submit gateway failed for agent: {submit_status.stdout}")

    print("PASS: unprivileged agent can use gateways but cannot execute the pred oracle")


if __name__ == "__main__":
    main()
