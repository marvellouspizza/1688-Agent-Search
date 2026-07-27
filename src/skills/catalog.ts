import {
  lstatSync,
  readFileSync,
  readdirSync,
  realpathSync,
  statSync,
} from "node:fs";
import { isAbsolute, join, relative, resolve } from "node:path";

export interface SkillEntry {
  readonly name: string;
  readonly description: string;
  readonly root: string;
}

export class SkillCatalog {
  readonly #roots: readonly string[];

  constructor(roots: readonly string[]) {
    this.#roots = roots.map((root) => resolve(root));
  }

  list(): SkillEntry[] {
    const entries = new Map<string, SkillEntry>();
    for (const configuredRoot of this.#roots) {
      let root: string;
      try {
        if (!statSync(configuredRoot).isDirectory()) continue;
        root = realpathSync(configuredRoot);
      } catch {
        continue;
      }
      for (const directory of readdirSync(root, { withFileTypes: true })) {
        if (!directory.isDirectory()) continue;
        const skillFile = join(root, directory.name, "SKILL.md");
        try {
          if (!lstatSync(skillFile).isFile()) continue;
        } catch {
          continue;
        }
        const entry = parseSkillEntry(skillFile, root);
        if (entries.has(entry.name)) throw new Error(`Skill 名称重复：${entry.name}`);
        entries.set(entry.name, entry);
      }
    }
    return [...entries.values()].sort((left, right) => left.name.localeCompare(right.name));
  }

  read(name: string, relativePath = "SKILL.md"): string {
    const entry = this.list().find((item) => item.name === name);
    if (!entry) throw new Error(`未找到项目 Skill：${name}`);
    const candidate = resolve(entry.root, relativePath);
    assertContained(entry.root, candidate);
    let resolved: string;
    try {
      resolved = realpathSync(candidate);
    } catch (error) {
      throw new Error("Skill 文件不存在", { cause: error });
    }
    assertContained(entry.root, resolved);
    const stat = statSync(resolved);
    if (!stat.isFile()) throw new Error("Skill 文件不存在");
    if (stat.size > 200_000) throw new Error("Skill 文件过大");
    return readFileSync(resolved, "utf8");
  }
}

function parseSkillEntry(skillFile: string, catalogRoot: string): SkillEntry {
  const resolvedFile = realpathSync(skillFile);
  assertContained(catalogRoot, resolvedFile);
  const text = readFileSync(resolvedFile, "utf8");
  if (!text.startsWith("---\n")) throw new Error(`Skill 缺少 YAML frontmatter：${skillFile}`);
  const closing = text.indexOf("---\n", 4);
  if (closing < 0) throw new Error(`Skill 缺少 YAML frontmatter：${skillFile}`);
  const frontmatter = text.slice(4, closing);
  const name = frontmatter.match(/^name:\s*(.+)$/mu)?.[1]?.trim() ?? "";
  const description = frontmatter.match(/^description:\s*(.+)$/mu)?.[1]?.trim() ?? "";
  if (!/^[a-z0-9][a-z0-9-]{0,63}$/u.test(name) || !description) {
    throw new Error(`Skill frontmatter 无效：${skillFile}`);
  }
  return { name, description, root: realpathSync(resolve(resolvedFile, "..")) };
}

function assertContained(root: string, candidate: string): void {
  const child = relative(realpathSync(root), candidate);
  if (child === "" || (!isAbsolute(child) && child !== ".." && !child.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`))) {
    return;
  }
  throw new Error("Skill 引用文件超出 Skill 目录");
}
