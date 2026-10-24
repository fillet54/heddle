// Runs the Eclipse Layout Kernel (elkjs) on a graph read from stdin and
// writes the laid-out graph to stdout. Both are ELK JSON.
//
// elkjs is resolved like any Node module: from a node_modules folder next to
// or above this file (run `npm install` in the repo root), or from NODE_PATH.
const ELK = require("elkjs/lib/elk.bundled.js");

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => (input += chunk));
process.stdin.on("end", async () => {
  try {
    const graph = JSON.parse(input);
    const laidOut = await new ELK().layout(graph);
    process.stdout.write(JSON.stringify(laidOut));
  } catch (err) {
    process.stderr.write(String((err && err.stack) || err) + "\n");
    process.exit(1);
  }
});
