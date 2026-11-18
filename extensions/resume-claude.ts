/**
 * /resume-claude — continue work from a Claude Code session in Pi.
 *
 * Usage:
 *   /resume-claude            open a searchable session picker (type to filter live)
 *   /resume-claude <words>    open the picker pre-filtered by those words
 *   /resume-claude latest     resume the newest session directly (aliases: continue, -c)
 *   /resume-claude <session-id>   resume that session directly
 * Headless has no picker: it prints ids to resume by id.
 *
 * The shared flow lives in resume-session.ts; this file only names the target.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { registerResumeCommand } from "./resume-session.ts";

export default function (pi: ExtensionAPI) {
	registerResumeCommand(pi, {
		tool: "claude",
		command: "resume-claude",
		product: "Claude Code",
	});
}
