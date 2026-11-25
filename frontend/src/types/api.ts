export type DeviceType =
  | "binary_input"
  | "analog_sensor"
  | "pulse_counter"
  | "switch_output"
  | "pwm_output"
  | "dac_output"
  | "rgb_led"
  | "led_matrix"
  | "virtual_switch";

export interface ComponentConfig {
  id: string;
  type: DeviceType;
  name: string;
  board: string;
  pin?: string | number;
  props?: Record<string, unknown>;
}

export interface BoardConfig {
  id: string;
  name: string;
  model: string;
  link?: { type?: string; [key: string]: unknown };
}

export interface QHomeConfig {
  device: { name?: string; node?: string; role?: "master" | "node"; master_host?: string };
  boards: BoardConfig[];
  components: ComponentConfig[];
}

export interface DeviceState {
  v?: unknown;
  txt?: string;
  t?: number;
}

export interface BoardState {
  online?: boolean;
  text?: string;
  model?: string | null;
  fw?: number | null;
}

export interface SystemSample {
  cpu?: number | null;
  load?: number[] | null;
  mem?: { pct?: number; used?: number; total?: number } | null;
  disk?: { pct?: number; used?: number; total?: number } | null;
  temp?: number | null;
  os_uptime?: number | null;
  ips?: string[];
  port?: number;
}

export interface DashboardSnapshot {
  rev: number;
  states: Record<string, DeviceState>;
  boards: Record<string, BoardState>;
  mqtt?: { mode?: string; ok?: boolean; text?: string };
  matrix?: Record<string, string>;
  mx?: Record<string, { mode?: string; auto?: boolean; left?: number }>;
  vars?: Record<string, unknown>;
  running?: number;
  uptime?: number;
  sys?: SystemSample;
  time?: { tz?: string; epoch?: number; offset?: number; label?: string; abbr?: string; exact?: boolean };
}

export interface DevicePropertyDefinition {
  key: string;
  default: unknown;
  [key: string]: unknown;
}

export interface DeviceTypeDefinition {
  cap: string | null;
  tag: string;
  kind: string;
  props: DevicePropertyDefinition[];
}

export interface BoardModel {
  label?: string;
  vref?: number;
}

export interface QHomeMeta {
  version: string;
  role: "master" | "node";
  sim: boolean;
  has_local: boolean;
  web_port: number;
  sys?: { board_model?: string; hostname?: string; python?: string; os?: string; arch?: string; cpus?: number };
  types: Record<DeviceType, DeviceTypeDefinition>;
  models: Record<string, BoardModel>;
  colors: string[];
  entity_fields?: Record<string, string[]>;
  matrix?: { modes?: string[]; anims?: string[]; icons?: Record<string, string> };
}

export interface ApiContext {
  config: QHomeConfig;
  meta: QHomeMeta;
  snapshot: DashboardSnapshot;
}
