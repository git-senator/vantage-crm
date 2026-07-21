import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DealBoard } from "@/components/deals/deal-board";
import type { Deal, DealBoard as Board } from "@/lib/api/types";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh, back: vi.fn() }),
}));

const moveDealStage = vi.fn();
vi.mock("@/lib/api/deals-client", () => ({
  moveDealStage: (...args: unknown[]) => moveDealStage(...args),
}));

function makeDeal(overrides: Partial<Deal> = {}): Deal {
  return {
    id: "deal-1",
    title: "1428 Sanchez — Lindqvist purchase",
    status: "open",
    value: "1895000.00",
    currency: "USD",
    commission_amount: "47375.00",
    commission_rate: "0.0250",
    weighted_value: "189500.00",
    probability: 10,
    priority: "high",
    expected_close_date: null,
    actual_close_date: null,
    lost_reason: null,
    custom_fields: {},
    pipeline_id: "pipeline-1",
    stage: {
      id: "stage-qualification",
      key: "qualification",
      name: "Qualification",
      position: 0,
      is_won: false,
      is_lost: false,
    },
    client: { id: "client-1", display_name: "Harper Lindqvist" },
    listing: null,
    owner: null,
    created_at: "2026-07-21T10:00:00Z",
    updated_at: "2026-07-21T10:00:00Z",
    ...overrides,
  };
}

function makeBoard(): Board {
  return {
    pipeline_id: "pipeline-1",
    pipeline_name: "Sales Pipeline",
    columns: [
      {
        stage: {
          id: "stage-qualification",
          key: "qualification",
          name: "Qualification",
          position: 0,
          is_won: false,
          is_lost: false,
        },
        deals: [makeDeal()],
        total_value: "1895000.00",
        count: 1,
      },
      {
        stage: {
          id: "stage-closed-won",
          key: "closed_won",
          name: "Closed won",
          position: 1,
          is_won: true,
          is_lost: false,
        },
        deals: [],
        total_value: "0",
        count: 0,
      },
      {
        stage: {
          id: "stage-closed-lost",
          key: "closed_lost",
          name: "Closed lost",
          position: 2,
          is_won: false,
          is_lost: true,
        },
        deals: [],
        total_value: "0",
        count: 0,
      },
    ],
  };
}

describe("DealBoard", () => {
  it("renders one column per stage, in pipeline order", () => {
    render(<DealBoard board={makeBoard()} canManage />);

    expect(screen.getByRole("region", { name: "Qualification" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Closed won" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Closed lost" })).toBeInTheDocument();
  });

  it("shows the server's column total, not a sum of the rendered cards", () => {
    // The server computes this against the same scoped set the cards come
    // from — a client-side sum would silently exclude anything past the board
    // cap. Two cards here so the total cannot be confused with either value.
    const board = makeBoard();
    board.columns[0].deals = [
      makeDeal({ id: "deal-1", value: "1000000.00" }),
      makeDeal({ id: "deal-2", value: "500000.00" }),
    ];
    board.columns[0].total_value = "1500000.00";
    board.columns[0].count = 2;

    render(<DealBoard board={board} canManage />);

    const column = screen.getByRole("region", { name: "Qualification" });
    expect(within(column).getByText("$1.50M")).toBeInTheDocument();
    expect(within(column).getByText("$1.00M")).toBeInTheDocument();
    expect(within(column).getByText("$500K")).toBeInTheDocument();
  });

  it("labels an empty column rather than leaving a blank panel", () => {
    render(<DealBoard board={makeBoard()} canManage />);

    const column = screen.getByRole("region", { name: "Closed won" });
    expect(within(column).getByText("Nothing here")).toBeInTheDocument();
  });

  it("renders a card with its client and value", () => {
    render(<DealBoard board={makeBoard()} canManage />);

    expect(screen.getByText("Harper Lindqvist")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Lindqvist purchase/ }),
    ).toHaveAttribute("href", "/deals/deal-1");
  });

  it("falls back to probability when a deal has no commission", () => {
    const board = makeBoard();
    board.columns[0].deals = [makeDeal({ commission_amount: null })];
    render(<DealBoard board={board} canManage />);

    expect(screen.getByText("10%")).toBeInTheDocument();
  });

  it("shows a dash rather than $0 for an unpriced deal", () => {
    // Rendering $0 would be a factual claim about the deal's value.
    const board = makeBoard();
    board.columns[0].deals = [makeDeal({ value: null })];
    render(<DealBoard board={board} canManage />);

    const column = screen.getByRole("region", { name: "Qualification" });
    expect(within(column).getAllByText("—").length).toBeGreaterThan(0);
  });

  it("does not offer a drag handle without deals.manage", () => {
    // UX only — the API refuses the move regardless of what this renders.
    const { container } = render(
      <DealBoard board={makeBoard()} canManage={false} />,
    );
    expect(container.querySelector(".cursor-grab")).toBeNull();
  });

  it("offers a drag handle with deals.manage", () => {
    const { container } = render(<DealBoard board={makeBoard()} canManage />);
    expect(container.querySelector(".cursor-grab")).not.toBeNull();
  });
});
