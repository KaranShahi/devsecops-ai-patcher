// Intentionally vulnerable demo app — DO NOT deploy.
// Used as a scan target for the DevSecOps static analysis & patching engine.

const express = require("express");
const mysql = require("mysql");
const { exec } = require("child_process");

const app = express();

// --- Vulnerability 1: hardcoded API key ---
const STRIPE_API_KEY = "REPLACE_ME_WITH_A_REAL_SECRET_VALUE_0000";

const db = mysql.createConnection({
  host: process.env.DB_HOST || "localhost",
  user: process.env.DB_USER || "root",
  password: process.env.DB_PASSWORD,
  database: process.env.DB_NAME || "app_db",
});

// --- Vulnerability 2: SQL query built via string concatenation ---
app.get("/user", (req, res) => {
  const userId = req.query.id;
  const query = "SELECT * FROM users WHERE id = " + userId;
  db.query(query, (err, results) => {
    if (err) return res.status(500).send(err);
    res.json(results);
  });
});

// --- Vulnerability 3: shell command built via string concatenation ---
app.get("/ping", (req, res) => {
  const host = req.query.host;
  exec("ping " + host, (err, stdout) => {
    if (err) return res.status(500).send("Ping failed");
    res.send(stdout);
  });
});

app.listen(3000, () => {
  console.log("Server running");
});
