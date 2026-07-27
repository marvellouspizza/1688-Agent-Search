#!/usr/bin/env node

import { runPurchaseCli } from "./cli.js";

process.exitCode = await runPurchaseCli();
