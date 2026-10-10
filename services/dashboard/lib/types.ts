export interface Me {
  username: string;
  role: "owner" | "viewer";
  csrf: string;
}

export interface SimState {
  source?: string;
  next_open?: string;
  session?: string;
  phase?: string;
  speed?: number;
  paused?: boolean;
  waiting_for_approval?: boolean;
  kill?: boolean;
  strategies?: string[];
}

export interface Status {
  sim: SimState;
  sim_now: number | null;
  kill: { active?: boolean; reason?: string; ts?: number };
  mode: string;
  pending_count: number;
}

export interface Account {
  equity?: number;
  cash?: number;
  start_equity?: number;
  day_pnl?: number;
  gross_exposure?: number;
  open_positions?: number;
  trades_today?: number;
}

export interface Check {
  name: string;
  ok: boolean;
  detail: string;
}

export interface Cond {
  cond: string;
  ok: boolean;
  value: unknown;
  detail?: string;
  missing?: boolean;
}
