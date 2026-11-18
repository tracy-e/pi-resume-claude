/**
 * /resume-codex — continue work from a Codex session in Pi.
 *
 * Usage:
 *   /resume-codex            open a searchable session picker (type to filter live)
 *   /resume-codex <words>    open the picker pre-filtered by those words
 *   /resume-codex latest     resume the newest session directly (aliases: continue, -c)
 *   /resume-codex <session-id>   resume that session directly
 * Headless has no picker: it prints ids to resume by id.
 *
 * The shared flow lives in resume-session.ts; this file only names the target.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { CODEX_MAX_TURNS, registerResumeCommand } from "./resume-session.ts";

export default function (pi: ExtensionAPI) {
	registerResumeCommand(pi, {
		tool: "codex",
		command: "resume-codex",
		product: "Codex",
		maxTurns: CODEX_MAX_TURNS,
	});
}
