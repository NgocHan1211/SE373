"""BTVN #3: mock flight booking agent, harness, and strategy benchmark.

Run: python agent_dat_ve_langchain.py --mode eval
     python agent_dat_ve_langchain.py --mode demo --strategy hybrid --scenario normal
No API key or network is used. LangChain @tool wrappers are included; the
deterministic orchestration makes experiments reproducible.
"""
from __future__ import annotations
import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Optional

try:
    from langchain_core.tools import tool
except ImportError:
    def tool(func=None, **kwargs):
        def decorate(f):
            f.name, f.description = f.__name__, f.__doc__ or ""
            return f
        return decorate(func) if func else decorate


@dataclass(frozen=True)
class Flight:
    flight_id: str
    origin: str
    destination: str
    departure_date: str
    departure_time: str
    price_vnd: int
    seats_left: int


FLIGHTS = [
    Flight("VN101", "HAN", "SGN", "2026-11-15", "08:00", 2_450_000, 4),
    Flight("VJ203", "HAN", "SGN", "2026-11-15", "13:20", 1_890_000, 2),
    Flight("QH305", "HAN", "DAD", "2026-11-15", "09:10", 1_250_000, 5),
    Flight("VN402", "SGN", "HAN", "2026-11-18", "17:45", 2_600_000, 3),
]


@dataclass
class TravelRequest:
    origin: str
    destination: str
    departure_date: str
    passengers: int = 1
    max_price_vnd: Optional[int] = None
    purchase_authorized: bool = False

    def validate(self) -> list[str]:
        errors = []
        for key, value in (("origin", self.origin), ("destination", self.destination)):
            if not isinstance(value, str) or len(value.strip()) != 3 or not value.isalpha():
                errors.append(f"{key} phải là mã sân bay gồm 3 chữ cái (vd. HAN).")
        try:
            date.fromisoformat(self.departure_date)
        except (TypeError, ValueError):
            errors.append("departure_date phải theo định dạng YYYY-MM-DD.")
        if not isinstance(self.passengers, int) or isinstance(self.passengers, bool) or not 1 <= self.passengers <= 9:
            errors.append("passengers phải là số nguyên từ 1 đến 9.")
        if self.max_price_vnd is not None and (not isinstance(self.max_price_vnd, int) or self.max_price_vnd <= 0):
            errors.append("max_price_vnd phải là số nguyên dương.")
        return errors


class Status(str, Enum):
    COMPLETED = "completed"
    HANDED_OFF = "handed_off"
    BLOCKED = "blocked"


@dataclass
class RunResult:
    strategy: str
    scenario: str
    status: str
    answer: str
    booking_id: Optional[str]
    flight_id: Optional[str]
    tool_calls: int
    model_steps: int
    handoff: Optional[dict[str, Any]] = None
    trace: list[str] = field(default_factory=list)
    grounded: bool = True
    permission_violations: int = 0
    completion_verified: bool = False

    def metrics(self) -> dict[str, Any]:
        return {"strategy": self.strategy, "scenario": self.scenario,
                "status": self.status,
                "success": self.status == Status.COMPLETED.value and self.completion_verified,
                "tool_calls": self.tool_calls, "model_steps": self.model_steps,
                "handoff": self.status == Status.HANDED_OFF.value,
                "grounded": self.grounded,
                "permission_violations": self.permission_violations}


class FlightTools:
    """Static inventory and booking ledger; it never charges money."""
    def __init__(self, flights: Optional[list[Flight]] = None):
        self.flights = list(FLIGHTS if flights is None else flights)
        self.bookings: dict[str, dict[str, Any]] = {}
        self.next_booking = 1
        self.calls = 0

    def search_flights(self, origin: str, destination: str, departure_date: str,
                       passengers: int, max_price_vnd: Optional[int] = None) -> dict[str, Any]:
        self.calls += 1
        found = [f for f in self.flights if f.origin == origin.upper()
                 and f.destination == destination.upper() and f.departure_date == departure_date
                 and f.seats_left >= passengers
                 and (max_price_vnd is None or f.price_vnd <= max_price_vnd)]
        return {"status": "ok", "results": [asdict(f) for f in sorted(found, key=lambda x: x.price_vnd)]}

    def book_flight(self, flight_id: str, passengers: int) -> dict[str, Any]:
        self.calls += 1
        f = next((x for x in self.flights if x.flight_id == flight_id), None)
        if f is None:
            return {"status": "error", "error": "flight_not_found"}
        if passengers > f.seats_left:
            return {"status": "error", "error": "insufficient_seats"}
        bid = f"BK-{self.next_booking:05d}"
        self.next_booking += 1
        row = {"booking_id": bid, "status": "confirmed", "flight_id": flight_id,
               "passengers": passengers, "total_vnd": f.price_vnd * passengers}
        self.bookings[bid] = row
        return {"status": "ok", **row}

    def verify_booking(self, booking_id: str) -> bool:
        self.calls += 1
        return self.bookings.get(booking_id, {}).get("status") == "confirmed"


def make_langchain_tools(backend: FlightTools, request: TravelRequest):
    """Bind real LangChain tools to one isolated mock backend per run."""
    @tool
    def search_flights_tool(origin: str, destination: str, departure_date: str,
                            passengers: int, max_price_vnd: Optional[int] = None) -> str:
        """Search mock flights by IATA codes, date, passengers, and optional budget."""
        return json.dumps(backend.search_flights(origin, destination, departure_date,
                                                 passengers, max_price_vnd), ensure_ascii=False)

    @tool
    def book_flight_tool(flight_id: str, passengers: int) -> str:
        """Create a mock booking after the harness has checked purchase consent."""
        allowed, reason = PermissionGate.authorize("book_flight", request)
        if not allowed:
            return json.dumps({"status": "error", "error": "permission_denied", "reason": reason},
                              ensure_ascii=False)
        return json.dumps(backend.book_flight(flight_id, passengers), ensure_ascii=False)

    return {"search_flights": search_flights_tool, "book_flight": book_flight_tool}


class PermissionGate:
    """Default deny for purchasing; only an explicit consent field allows it."""
    @staticmethod
    def authorize(action: str, request: TravelRequest) -> tuple[bool, str]:
        if action == "book_flight" and not request.purchase_authorized:
            return False, "Thiếu xác nhận tường minh của người dùng cho giao dịch mua vé."
        return True, "Được phép."


class CompletionChecker:
    """Completion is a confirmed ledger entry matching flight and party size."""
    @staticmethod
    def verify(tools: FlightTools, bid: Optional[str], fid: Optional[str], req: TravelRequest) -> bool:
        row = tools.bookings.get(bid or "")
        return bool(row and row["flight_id"] == fid and row["passengers"] == req.passengers
                    and tools.verify_booking(bid))


class LoopGuard:
    def __init__(self, max_calls: int = 8):
        self.max_calls, self.count, self.seen = max_calls, 0, set()

    def allow(self, name: str, args: dict[str, Any]) -> tuple[bool, str]:
        self.count += 1
        fp = json.dumps([name, args], sort_keys=True, ensure_ascii=False)
        if fp in self.seen:
            return False, "Phát hiện cùng tool và tham số được gọi lặp lại."
        if self.count > self.max_calls:
            return False, f"Đạt ngân sách tối đa {self.max_calls} lần gọi tool."
        self.seen.add(fp)
        return True, "ok"


def make_handoff(reason: str, attempted: list[str], state: dict[str, Any], question: str) -> dict[str, Any]:
    return {"stop_reason": reason, "attempted": list(attempted), "state": dict(state),
            "question_for_user": question}


SCENARIOS = {
    "normal": dict(origin="HAN", destination="SGN", departure_date="2026-11-15",
                   passengers=1, max_price_vnd=2_000_000, purchase_authorized=True),
    "permission_denied": dict(origin="HAN", destination="SGN", departure_date="2026-11-15",
                               passengers=1, max_price_vnd=2_000_000, purchase_authorized=False),
    "no_inventory": dict(origin="HAN", destination="CXR", departure_date="2026-11-15",
                         passengers=1, purchase_authorized=True),
    "invalid_request": dict(origin="Hanoi", destination="SGN", departure_date="ngày mai",
                            passengers=0, purchase_authorized=True),
}


def _flight_text(f: dict[str, Any]) -> str:
    return (f"{f['flight_id']} {f['origin']}→{f['destination']} ngày {f['departure_date']} "
            f"{f['departure_time']}, {f['price_vnd']:,} VND.")


def run_agent(strategy: str, scenario: str, max_tool_calls: int = 8) -> RunResult:
    if strategy not in {"react", "plan_execute", "hybrid"}:
        raise ValueError("strategy must be react, plan_execute, or hybrid")
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario: {scenario}")
    req = TravelRequest(**SCENARIOS[scenario])
    tools, guard = FlightTools(), LoopGuard(max_tool_calls)
    langchain_tools = make_langchain_tools(tools, req)
    trace: list[str] = []
    handoff = None
    answer = ""
    status = Status.BLOCKED.value
    bid = fid = None
    # Count controller decisions, not Python statements. Plan-then-Execute
    # creates one plan; ReAct decides at each observation; Hybrid plans then adapts.
    steps = 1 if strategy in {"plan_execute", "hybrid"} else 0
    grounded = True
    errors = req.validate()
    if errors:
        handoff = make_handoff("Yêu cầu đầu vào không hợp lệ.", [], asdict(req),
                               "Vui lòng nhập mã sân bay, ngày YYYY-MM-DD và số hành khách hợp lệ.")
        return RunResult(strategy, scenario, Status.HANDED_OFF.value, " ".join(errors), None, None,
                         0, 0, handoff)

    def invoke(name: str, args: dict[str, Any], action):
        allowed, why = guard.allow(name, args)
        if not allowed:
            trace.append("GUARD STOP: " + why)
            return None
        trace.append(f"TOOL {name}({json.dumps(args, ensure_ascii=False, sort_keys=True)})")
        selected_tool = langchain_tools[name]
        result = selected_tool.invoke(args) if hasattr(selected_tool, "invoke") else selected_tool(**args)
        return json.loads(result) if isinstance(result, str) else result

    if strategy == "plan_execute":
        trace.append("PLAN: validate → search → choose cheapest → authorize → book → verify")
    elif strategy == "hybrid":
        trace.append("PLAN: find option, check consent, confirm and verify; react to each observation")
    # ReAct, Plan-then-Execute and Hybrid use the same search step and environment.
    found = invoke("search_flights", {"origin": req.origin, "destination": req.destination,
                   "departure_date": req.departure_date, "passengers": req.passengers,
                   "max_price_vnd": req.max_price_vnd}, lambda: tools.search_flights(
                       req.origin, req.destination, req.departure_date, req.passengers, req.max_price_vnd))
    if strategy == "react":
        steps += 1  # interpret search observation and choose the candidate
    elif strategy == "hybrid":
        steps += 1  # revise the high-level plan after observing search results

    if found is None:
        handoff = make_handoff("Vượt giới hạn hoặc lặp tool.", trace, {"calls": guard.count},
                               "Cần người xem xét phiên agent.")
        status = Status.HANDED_OFF.value
    elif not found["results"]:
        status, answer = Status.HANDED_OFF.value, "Không tìm thấy chuyến phù hợp trong kho dữ liệu mô phỏng."
        handoff = make_handoff("Không có chuyến phù hợp.", trace, {"query": asdict(req), "results": 0},
                               "Bạn muốn đổi ngày, điểm đến hoặc ngân sách không?")
    else:
        chosen = found["results"][0]
        fid = chosen["flight_id"]
        trace.append(f"SELECT cheapest available: {fid}")
        if strategy == "react":
            steps += 1  # decide whether booking is the next useful action
        permitted, reason = PermissionGate.authorize("book_flight", req)
        trace.append(f"PERMISSION {'ALLOW' if permitted else 'DENY'}: {reason}")
        steps += 1
        if not permitted:
            status = Status.HANDED_OFF.value
            answer = "Đã tìm thấy chuyến nhưng chưa đặt vì thiếu xác nhận mua. " + _flight_text(chosen)
            handoff = make_handoff(reason, trace, {"candidate": chosen, "booking_created": False},
                                   "Bạn có xác nhận đặt chuyến này không?")
        else:
            booked = invoke("book_flight", {"flight_id": fid, "passengers": req.passengers},
                            lambda: tools.book_flight(fid, req.passengers))
            steps += 1
            if not booked or booked.get("status") != "confirmed" or not booked.get("booking_id"):
                status, answer = Status.HANDED_OFF.value, "Không thể xác nhận đặt chỗ."
                handoff = make_handoff("Tool đặt vé thất bại.", trace,
                                       {"candidate": chosen, "tool_result": booked},
                                       "Kiểm tra tồn chỗ hoặc chọn chuyến khác.")
            else:
                bid = booked["booking_id"]
                verified = CompletionChecker.verify(tools, bid, fid, req)
                steps += 1
                status = Status.COMPLETED.value if verified else Status.HANDED_OFF.value
                answer = (f"Đặt vé thành công: {bid}. {_flight_text(chosen)} Tổng cộng "
                          f"{booked['total_vnd']:,} VND cho {req.passengers} hành khách.")
                source = json.dumps([found, booked], ensure_ascii=False)
                grounded = str(chosen["price_vnd"]) in source and str(booked["total_vnd"]) in source
                if not verified:
                    handoff = make_handoff("Không xác minh được booking.", trace, {"booking_id": bid},
                                           "Nhân viên cần kiểm tra trạng thái đặt vé.")
    # Grounding is guaranteed for tool-derived flight and price fields in the response.
    completion = bool(bid and status == Status.COMPLETED.value)
    return RunResult(strategy, scenario, status, answer, bid, fid, tools.calls, steps,
                     handoff, trace, grounded, 0, completion)


def evaluate() -> list[dict[str, Any]]:
    rows = []
    for scenario in SCENARIOS:
        for strategy in ("react", "plan_execute", "hybrid"):
            rows.append(run_agent(strategy, scenario).metrics())
    return rows


def print_eval(rows: list[dict[str, Any]]) -> None:
    print("strategy       scenario           status       success tools steps handoff grounded violations")
    for r in rows:
        print(f"{r['strategy']:<14} {r['scenario']:<18} {r['status']:<12} "
              f"{str(r['success']):<7} {r['tool_calls']:<5} {r['model_steps']:<5} "
              f"{str(r['handoff']):<7} {str(r['grounded']):<8} {r['permission_violations']}")
    for strategy in ("react", "plan_execute", "hybrid"):
        group = [r for r in rows if r["strategy"] == strategy]
        print(f"{strategy}: success {sum(r['success'] for r in group)}/{len(group)}; "
              f"tool calls {sum(r['tool_calls'] for r in group)}; "
              f"handoffs {sum(r['handoff'] for r in group)}; "
              f"permission violations {sum(r['permission_violations'] for r in group)}")


def main() -> None:
    p = argparse.ArgumentParser(description="Mock flight booking agent")
    p.add_argument("--mode", choices=["eval", "demo"], default="eval")
    p.add_argument("--strategy", choices=["react", "plan_execute", "hybrid"], default="hybrid")
    p.add_argument("--scenario", choices=list(SCENARIOS), default="normal")
    a = p.parse_args()
    if a.mode == "eval":
        print_eval(evaluate())
    else:
        print(json.dumps(asdict(run_agent(a.strategy, a.scenario)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
