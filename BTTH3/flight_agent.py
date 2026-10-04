"""BTVN #3 — teacher-style LangChain flight agent.

Live run (needs an OpenRouter key and an OpenRouter model slug):
    python flight_agent.py --strategy react --approve
    python flight_agent.py --strategy plan_then_execute --approve
    python flight_agent.py --strategy hybrid --approve

No real booking/payment is made. Inventory and booking ledger are in memory.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, asdict
from time import perf_counter
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI


# Constraints are DATA. The model receives them, but Python checks them too.
@dataclass
class Constraints:
    origin: str = "SGN"
    destination: str = "DAD"
    date: str = "2026-10-07"
    depart_before: str = "12:00"
    max_price: int = 2_000_000
    passengers: int = 1
    purchase_approved: bool = False

    def to_prompt(self) -> str:
        return (f"Find {self.passengers} ticket(s) from {self.origin} to {self.destination} "
                f"on {self.date}, departing before {self.depart_before}, price at most "
                f"{self.max_price:,} VND. Purchase approval: {self.purchase_approved}.")

    def is_ok(self, flight: dict[str, Any]) -> bool:
        return (flight["origin"] == self.origin and flight["destination"] == self.destination
                and flight["date"] == self.date and flight["depart"] < self.depart_before
                and flight["price"] <= self.max_price
                and flight["seats"] >= self.passengers)


FLIGHTS = [
    {"id": "VN142", "origin": "SGN", "destination": "DAD", "date": "2026-10-07",
     "depart": "08:15", "price": 1_650_000, "seats": 4},
    {"id": "VJ628", "origin": "SGN", "destination": "DAD", "date": "2026-10-07",
     "depart": "13:20", "price": 1_250_000, "seats": 3},
    {"id": "VN125", "origin": "SGN", "destination": "DAD", "date": "2026-10-07",
     "depart": "09:40", "price": 2_450_000, "seats": 2},
]


class Harness:
    """Plain Python owns permission, completion, and human handoff decisions."""
    def __init__(self, constraints: Constraints):
        self.constraints = constraints
        self.bookings: list[dict[str, Any]] = []
        self.trace: list[str] = []

    def check_permission(self, flight: dict[str, Any]) -> tuple[bool, str]:
        if not self.constraints.is_ok(flight):
            return False, "Flight violates one or more constraints."
        if not self.constraints.purchase_approved:
            return False, "User has not approved purchase."
        return True, "Allowed."

    def is_done(self) -> bool:
        """Read booking data back; never trust the model's final sentence."""
        return any(b["status"] == "CONFIRMED"
                   and b["flight"]["origin"] == self.constraints.origin
                   and b["flight"]["destination"] == self.constraints.destination
                   and b["flight"]["date"] == self.constraints.date
                   and self.constraints.is_ok(b["flight"])
                   and b["passengers"] == self.constraints.passengers
                   for b in self.bookings)

    def handoff(self, reason: str) -> dict[str, Any]:
        return {"status": "FAILED + HANDOFF", "reason": reason,
                "constraints": asdict(self.constraints), "attempted": self.trace,
                "booking": self.bookings,
                "question": "Would you like to change the constraints or ask a human agent?"}


def make_tools(c: Constraints, h: Harness):
    """Create per-run tools so they share only this session's mock state."""
    @tool
    def search_flights() -> str:
        """Search the mock inventory and return flights satisfying the request."""
        h.trace.append("search_flights")
        found = [f for f in FLIGHTS if c.is_ok(f)]
        return json.dumps(found, ensure_ascii=False)

    @tool
    def book_flight(flight_id: str) -> str:
        """Book one listed flight. Harness checks constraints and consent first."""
        h.trace.append(f"book_flight({flight_id})")
        flight = next((f for f in FLIGHTS if f["id"] == flight_id), None)
        if flight is None:
            return json.dumps({"status": "ERROR", "reason": "Unknown flight id"})
        allowed, reason = h.check_permission(flight)
        if not allowed:
            return json.dumps({"status": "BLOCKED", "reason": reason})
        booking = {"booking_id": f"BK-{len(h.bookings) + 1:04d}",
                   "status": "CONFIRMED", "flight": flight,
                   "passengers": c.passengers,
                   "total": flight["price"] * c.passengers}
        h.bookings.append(booking)
        return json.dumps(booking, ensure_ascii=False)

    return [search_flights, book_flight]


def run(strategy: str, c: Constraints) -> dict[str, Any]:
    model_name = os.getenv("OPENROUTER_MODEL", "google/gemma-4-26b-a4b-it")
    if not os.getenv("OPENROUTER_API_KEY"):
        raise SystemExit("Set OPENROUTER_API_KEY before live mode.")
    # OpenRouter exposes an OpenAI-compatible endpoint. ChatOpenAI uses that
    # stable protocol while LangChain still manages the agent/tool loop.
    model = ChatOpenAI(
        model=model_name,
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
        temperature=0,
        default_headers={
            "HTTP-Referer": "http://localhost",
            "X-OpenRouter-Title": "BTVN3 Flight Agent",
        },
    )
    h = Harness(c)
    tools = make_tools(c, h)
    # PTE spends one model call creating the plan, so leave nine for the executor.
    limit = ModelCallLimitMiddleware(run_limit=9 if strategy == "plan_then_execute" else 10)

    started = perf_counter()
    extra_model_calls = 0
    if strategy == "react":
        # LangChain runs the ReAct model → tool → observation loop.
        agent = create_agent(model=model, tools=tools,
                             system_prompt="Use tools for facts. Search, inspect results, then act. "
                                           "Never claim booking unless book_flight confirms it. " + c.to_prompt(),
                             middleware=[limit])
        result = agent.invoke({"messages": [{"role": "user", "content": c.to_prompt()}]})
    elif strategy == "plan_then_execute":
        # First make a plan without tools, then give that plan to a tool-enabled executor.
        plan = model.invoke("Return a short numbered plan for this request. Do not claim success.\n"
                            + c.to_prompt()).content
        extra_model_calls = 1
        agent = create_agent(model=model, tools=tools,
                             system_prompt="Execute the supplied plan one step at a time. "
                                           "Tool output is the only source of flight/booking facts. "
                                           "Never claim booking unless the booking tool confirms it. " + c.to_prompt(),
                             middleware=[limit])
        result = agent.invoke({"messages": [{"role": "user",
                                              "content": f"Plan:\n{plan}\n\nRequest:\n{c.to_prompt()}"}]})
        h.trace.insert(0, f"plan: {plan}")
    elif strategy == "hybrid":
        # One agent plans at a high level, then revises its next action from observations.
        agent = create_agent(model=model, tools=tools,
                             system_prompt="First form a brief internal plan. Then act with tools, "
                                           "revising the next step from each observation. "
                                           "Never claim booking unless the booking tool confirms it. " + c.to_prompt(),
                             middleware=[limit])
        result = agent.invoke({"messages": [{"role": "user", "content": c.to_prompt()}]})
    else:
        raise ValueError("strategy must be react, plan_then_execute, or hybrid")

    answer = result["messages"][-1].content
    model_calls = extra_model_calls + sum(1 for m in result["messages"]
                                          if getattr(m, "type", "") == "ai")
    elapsed = round(perf_counter() - started, 2)
    tool_calls = sum(1 for item in h.trace if item.startswith(("search_flights", "book_flight(")))
    if h.is_done():
        return {"status": "DONE", "answer": answer, "booking": h.bookings,
                "trace": h.trace, "strategy": strategy, "model_calls": model_calls,
                "tool_calls": tool_calls, "seconds": elapsed}
    return {**h.handoff("No verified booking was created."), "answer": answer,
            "strategy": strategy, "model_calls": model_calls,
            "tool_calls": tool_calls, "seconds": elapsed}


def compare(case: str, approve: bool) -> None:
    """Run each real strategy on the same scenario and print comparable metrics."""
    destination = "CXR" if case == "no_inventory" else "DAD"
    purchase_approved = case == "success" and approve
    expected_done = case == "success" and purchase_approved
    print("strategy             status              done  expected  model_calls  tool_calls  seconds")
    for strategy in ("react", "plan_then_execute", "hybrid"):
        c = Constraints(destination=destination, purchase_approved=purchase_approved)
        result = run(strategy, c)
        done = result["status"] == "DONE"
        print(f"{strategy:<20} {result['status']:<19} {str(done):<5} "
              f"{str(expected_done):<9} {result['model_calls']:<12} "
              f"{result['tool_calls']:<11} {result['seconds']}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--strategy", choices=["react", "plan_then_execute", "hybrid", "all"], default="react")
    p.add_argument("--approve", action="store_true", help="explicitly authorize the mock purchase")
    p.add_argument("--case", choices=["success", "permission_denied", "no_inventory"], default="success")
    a = p.parse_args()
    if a.strategy == "all":
        compare(a.case, a.approve)
    else:
        c = Constraints(purchase_approved=(a.approve and a.case == "success"))
        if a.case == "no_inventory":
            c.destination = "CXR"
        print(json.dumps(run(a.strategy, c), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
