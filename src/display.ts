export interface SpinnerOutput {
  readonly isTTY?: boolean;
  write(chunk: string): unknown;
}

export const SPINNERS = {
  dots: ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"],
  star: ["✶", "✷", "✸", "✹", "✺", "✹", "✸", "✷"],
  moon: ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"],
  pulse: ["◜", "◠", "◝", "◞", "◡", "◟"],
  brain: ["🧠", "💭", "💡", "✨", "💫", "🌟", "💡", "💭"],
  sparkle: ["⁺", "˚", "*", "✧", "✦", "✧", "*", "˚"],
} as const;

const THINKING_FACES = [
  "(｡•́︿•̀｡)", "(◔_◔)", "(¬‿¬)", "( •_•)>⌐■-■", "(⌐■_■)", "(´･_･`)", "◉_◉",
  "(°ロ°)", "( ˘⌣˘)♡", "ヽ(>∀<☆)☆", "٩(๑❛ᴗ❛๑)۶", "(⊙_⊙)", "(¬_¬)", "( ͡° ͜ʖ ͡°)", "ಠ_ಠ",
] as const;
const THINKING_VERBS = [
  "pondering", "contemplating", "musing", "cogitating", "ruminating", "deliberating", "mulling",
  "reflecting", "processing", "reasoning", "analyzing", "computing", "synthesizing", "formulating", "brainstorming",
] as const;

export class HermesThinkingSpinner {
  readonly message: string;
  readonly #frames: readonly string[];
  readonly #output: SpinnerOutput;
  #timer: NodeJS.Timeout | undefined;
  #frame = 0;
  #startedAt = 0;
  #lastLength = 0;

  constructor(
    message: string,
    spinnerType: keyof typeof SPINNERS = "dots",
    output: SpinnerOutput = process.stdout,
  ) {
    this.message = message;
    this.#frames = SPINNERS[spinnerType] ?? SPINNERS.dots;
    this.#output = output;
  }

  static createForModelRequest(output: SpinnerOutput = process.stdout): HermesThinkingSpinner {
    const face = randomItem(THINKING_FACES);
    const verb = randomItem(THINKING_VERBS);
    const spinner = randomItem(["brain", "sparkle", "pulse", "moon", "star"] as const);
    return new HermesThinkingSpinner(`${face} ${verb}...`, spinner, output);
  }

  start(): void {
    if (this.#timer) return;
    this.#startedAt = performance.now();
    if (!this.#output.isTTY) {
      this.#write(`  [tool] ${this.message}\n`);
      this.#timer = setInterval(() => {}, 2_147_000_000);
      this.#timer.unref();
      return;
    }
    this.#render();
    this.#timer = setInterval(() => this.#render(), 120);
    this.#timer.unref();
  }

  stop(): void {
    if (!this.#timer) return;
    clearInterval(this.#timer);
    this.#timer = undefined;
    if (this.#output.isTTY) this.#write(`\r${" ".repeat(Math.max(this.#lastLength + 5, 40))}\r`);
  }

  #render(): void {
    if (process.env.HERMES_SPINNER_PAUSE) return;
    const frame = this.#frames[this.#frame % this.#frames.length] ?? "⠋";
    const elapsed = (performance.now() - this.#startedAt) / 1_000;
    const line = `  ${frame} ${this.message} (${elapsed.toFixed(1)}s)`;
    this.#write(`\r${line}${" ".repeat(Math.max(this.#lastLength - line.length, 0))}`);
    this.#lastLength = line.length;
    this.#frame += 1;
  }

  #write(text: string): void {
    try { this.#output.write(text); } catch { /* display failures never fail a model request */ }
  }
}

function randomItem<T>(items: readonly T[]): T {
  return items[Math.floor(Math.random() * items.length)]!;
}
