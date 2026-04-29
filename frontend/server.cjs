const http = require("http");
const next = require("next");

const port = Number(process.env.PORT || "3000");
const hostname = "0.0.0.0";
const app = next({ dev: false, hostname, port });
const handle = app.getRequestHandler();

app
  .prepare()
  .then(() => {
    http
      .createServer((req, res) => {
        handle(req, res);
      })
      .listen(port, hostname, () => {
        console.log(`Next.js server listening on http://${hostname}:${port}`);
      });
  })
  .catch((error) => {
    console.error("Failed to start Next.js server", error);
    process.exit(1);
  });
