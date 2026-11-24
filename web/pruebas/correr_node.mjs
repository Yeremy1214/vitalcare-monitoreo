// Corre con Node las pruebas que no necesitan ventana (30 de 32):
//   node web/pruebas/correr_node.mjs
// Las dos de la ventana se corren abriendo /pruebas/ en el navegador.
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { definirPruebas, correr } from "./pruebas.js";

const aqui = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const initSqlJs = require(path.join(aqui, "..", "vendor", "sql-wasm.js"));
const SQL = await initSqlJs({ locateFile: (f) => path.join(aqui, "..", "vendor", f) });

const pruebas = definirPruebas({ SQL, conVentana: false });
const fallos = await correr(pruebas, (r) => {
  console.log(`  ${r.ok ? "OK   " : "FALLA"} ${r.nombre}`);
  if (!r.ok) console.log(r.error.split("\n").map((l) => "        " + l).join("\n"));
});
console.log(`\n${pruebas.length - fallos} de ${pruebas.length} pruebas correctas (las 2 de la ventana se corren en /pruebas/ del navegador)`);
process.exit(fallos ? 1 : 0);
