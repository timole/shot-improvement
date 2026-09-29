// Compile JSX ahead of deployment; browsers no longer download/run Babel.
const fs = require("fs");
const vm = require("vm");
const path = require("path");
(async () => {
  const response = await fetch("https://unpkg.com/@babel/standalone@7.26.9/babel.min.js");
  if (!response.ok) throw new Error(`Babel download: ${response.status}`);
  const context = {};
  vm.runInNewContext(await response.text(), context);
  const root = path.resolve(__dirname, "..");
  const source = fs.readFileSync(path.join(root, "web/app.js"), "utf8");
  const result = context.Babel.transform(source, { presets: ["react"], minified: true, comments: false });
  fs.writeFileSync(path.join(root, "web/app.bundle.js"), result.code + "\n");
  console.log("Built web/app.bundle.js");
})().catch(error => { console.error(error); process.exitCode = 1; });
