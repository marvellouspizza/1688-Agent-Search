import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { getPurchaseHome, type PathOptions } from "./config.js";

export const DEFAULT_SOUL = `# Identity
你协助用户进行采购调研与供应商信息整理。

# Defaults
面对采购需求时，优先确认规格、数量、单位和交付限制；需要公开链接或候选商家时使用可用搜索工具。
明确区分搜索候选信息与已核验的库存、价格、发票资质和商家身份，不确定时如实说明。
`;

export interface SoulOptions extends PathOptions {
  readonly appHome?: string;
}

export function getSoulPath(options: SoulOptions = {}): string {
  return join(options.appHome ?? getPurchaseHome(options), "SOUL.md");
}

export function loadOrSeedSoul(options: SoulOptions = {}): string {
  const path = getSoulPath(options);
  if (!existsSync(path)) {
    mkdirSync(options.appHome ?? getPurchaseHome(options), { recursive: true, mode: 0o700 });
    writeFileSync(path, DEFAULT_SOUL, { encoding: "utf8", mode: 0o600 });
    chmodSync(path, 0o600);
  }
  const content = readFileSync(path, "utf8").trim();
  return content || DEFAULT_SOUL.trim();
}
