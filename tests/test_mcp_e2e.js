#!/usr/bin/env node
/**
 * End-to-end MCP server tests with regtest.
 *
 * Starts a regtest bitcoind + HTTP server, then exercises the full MCP flow:
 *   generate_taproot_wallet (regtest, with backup)
 *   → fund via faucet HTTP API
 *   → check_balance (verify funded)
 *   → sweep_wallet (key-path spend)
 *   → check_balance (verify swept)
 *   → recover_wallet (script-path spend via backup key)
 *   → check_balance (verify recovered)
 *
 * Also tests generate_segwit_wallet + sweep on regtest.
 *
 * Requires: Bitcoin Core (bitcoind + bitcoin-cli) in PATH, npm install in mcp/.
 *
 * Usage:
 *     node tests/test_mcp_e2e.js
 */

'use strict';

const fs    = require('fs');
const path  = require('path');
const http  = require('http');
const { execSync, spawn } = require('child_process');

const SDK_ROOT = path.join(__dirname, '..', 'mcp', 'node_modules', '@modelcontextprotocol', 'sdk', 'dist', 'cjs');
const { Client }               = require(path.join(SDK_ROOT, 'client', 'index.js'));
const { StdioClientTransport } = require(path.join(SDK_ROOT, 'client', 'stdio.js'));

const PROJECT_ROOT = path.resolve(__dirname, '..');
const SERVER_PATH  = path.join(PROJECT_ROOT, 'mcp', 'mcp_server.js');

let passed = 0;
let failed = 0;

function pass(name) {
    passed++;
    console.log(`  PASS: ${name}`);
}

function fail(name, err) {
    failed++;
    console.log(`  FAIL: ${name} — ${err}`);
}

function assert(condition, msg) {
    if (!condition) throw new Error(msg);
}

function callToolJSON(client, name, args) {
    return client.callTool({ name, arguments: args || {} }).then(r => {
        assert(r.content && r.content.length > 0, 'Empty content');
        return JSON.parse(r.content[0].text);
    });
}

// ── HTTP helper ──────────────────────────────────────────────────────────────

function httpPost(url, payload) {
    return new Promise((resolve, reject) => {
        const u = new URL(url);
        const data = JSON.stringify(payload);
        const req = http.request({
            hostname: u.hostname,
            port: u.port,
            path: u.pathname,
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) },
        }, res => {
            let body = '';
            res.on('data', chunk => body += chunk);
            res.on('end', () => {
                try { resolve(JSON.parse(body)); }
                catch (_) { reject(new Error(`Bad JSON: ${body}`)); }
            });
        });
        req.on('error', reject);
        req.write(data);
        req.end();
    });
}

// ── Regtest server (Python) ──────────────────────────────────────────────────

function findFreePort() {
    return new Promise((resolve, reject) => {
        const srv = require('net').createServer();
        srv.listen(0, '127.0.0.1', () => {
            const port = srv.address().port;
            srv.close(() => resolve(port));
        });
        srv.on('error', reject);
    });
}

function startRegtestServer(port) {
    const proc = spawn('python3', [
        path.join(PROJECT_ROOT, 'server', 'server.py'),
        String(port), '--regtest',
    ], {
        cwd: PROJECT_ROOT,
        stdio: ['ignore', 'pipe', 'pipe'],
    });

    // Drain stdout/stderr to prevent pipe buffer deadlock
    proc.stdout.on('data', () => {});
    proc.stderr.on('data', () => {});

    return proc;
}

async function waitForServer(baseUrl, timeoutMs = 60000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        try {
            const resp = await fetch(`${baseUrl}/api/health`, { signal: AbortSignal.timeout(2000) });
            if (resp.ok) {
                const data = await resp.json();
                if (data.regtest) return;
            }
        } catch (_) {}
        await new Promise(r => setTimeout(r, 1000));
    }
    throw new Error('Regtest server failed to start');
}

// ── Main ─────────────────────────────────────────────────────────────────────

async function main() {
    console.log('============================================================');
    console.log('E2E MCP Server Tests (regtest)');
    console.log('============================================================');

    // Check prerequisites
    try {
        execSync('which bitcoind', { stdio: 'ignore' });
        execSync('which bitcoin-cli', { stdio: 'ignore' });
    } catch (_) {
        console.log('ERROR: bitcoind/bitcoin-cli not found. Install Bitcoin Core.');
        process.exit(1);
    }

    const port = await findFreePort();
    const baseUrl = `http://127.0.0.1:${port}`;
    let serverProc = null;
    let client = null;
    const generatedFiles = [];

    try {
        // Start regtest server
        console.log(`\n  Starting regtest server on port ${port}...`);
        serverProc = startRegtestServer(port);
        await waitForServer(baseUrl);
        console.log('  Regtest server ready.');

        // Connect MCP client with REGTEST_SERVER_URL
        const transport = new StdioClientTransport({
            command: 'node',
            args: [SERVER_PATH],
            cwd: PROJECT_ROOT,
            env: { ...process.env, REGTEST_SERVER_URL: baseUrl },
        });
        client = new Client({ name: 'e2e-test', version: '1.0.0' });
        await client.connect(transport);
        console.log('  MCP client connected.\n');

        // ── Test 1: SegWit generate + fund + check_balance + sweep ────────

        try {
            console.log('--- Test 1: SegWit generate → fund → balance → sweep ---');

            // Generate
            const gen = await callToolJSON(client, 'generate_segwit_wallet', {
                network: 'regtest', open_preview: false,
            });
            assert(gen.address.startsWith('bcrt1q'), `Bad address: ${gen.address}`);
            generatedFiles.push(gen.bill_image, gen.metadata_json);
            console.log(`  Generated: ${gen.address}`);

            // Fund via faucet
            const fund = await httpPost(`${baseUrl}/api/faucet`, {
                address: gen.address, amount: 1.0,
            });
            assert(fund.success || fund.txid, `Faucet failed: ${JSON.stringify(fund)}`);
            console.log(`  Funded: 1.0 BTC`);

            // Check balance
            const bal = await callToolJSON(client, 'check_balance', {
                address: gen.address, network: 'regtest',
            });
            assert(bal.balance_sats === 100_000_000, `Expected 1 BTC, got ${bal.balance_sats} sats`);
            assert(bal.utxo_count === 1, `Expected 1 UTXO, got ${bal.utxo_count}`);
            console.log(`  Balance: ${bal.balance_sats} sats (${bal.utxo_count} UTXO)`);

            // Generate a destination address
            const dest = await callToolJSON(client, 'generate_segwit_wallet', {
                network: 'regtest', open_preview: false,
            });
            generatedFiles.push(dest.bill_image, dest.metadata_json);

            // Sweep
            const sweep = await callToolJSON(client, 'sweep_wallet', {
                wif: gen.private_key_wif,
                destination: dest.address,
                fee_rate: 2,
                network: 'regtest',
            });
            assert(sweep.status === 'broadcast', `Sweep failed: ${JSON.stringify(sweep)}`);
            assert(sweep.txid, 'Missing txid');
            assert(sweep.amount_sats > 0, `Bad amount: ${sweep.amount_sats}`);
            console.log(`  Swept: ${sweep.amount_sats} sats, fee=${sweep.fee_sats}, txid=${sweep.txid.slice(0, 16)}...`);

            // Verify source is now empty
            const balAfter = await callToolJSON(client, 'check_balance', {
                address: gen.address, network: 'regtest',
            });
            assert(balAfter.balance_sats === 0, `Source should be empty, got ${balAfter.balance_sats}`);

            // Verify destination has funds
            const balDest = await callToolJSON(client, 'check_balance', {
                address: dest.address, network: 'regtest',
            });
            assert(balDest.balance_sats === sweep.amount_sats,
                `Dest balance ${balDest.balance_sats} != swept ${sweep.amount_sats}`);
            console.log(`  Verified: source=0, dest=${balDest.balance_sats} sats`);

            pass('SegWit: generate → fund → balance → sweep → verify');
        } catch (e) { fail('SegWit E2E', e.message); }

        // ── Test 2: Taproot+backup generate + fund + sweep + recover ──────

        try {
            console.log('\n--- Test 2: Taproot+backup generate → fund → sweep → recover ---');

            // Generate wallet with backup key
            const gen = await callToolJSON(client, 'generate_taproot_wallet', {
                network: 'regtest', backup_key: true, open_preview: false,
            });
            assert(gen.address.startsWith('bcrt1p'), `Bad address: ${gen.address}`);
            assert(gen.has_backup_key === true, 'Missing backup key');
            assert(gen.backup_private_key_wif, 'Missing backup WIF');
            assert(gen.internal_pubkey_hex, 'Missing internal pubkey');
            generatedFiles.push(gen.bill_image, gen.metadata_json);
            const addr1 = gen.address;
            console.log(`  Generated: ${addr1} (with backup)`);

            // Fund
            await httpPost(`${baseUrl}/api/faucet`, { address: addr1, amount: 1.0 });
            console.log('  Funded: 1.0 BTC');

            // Generate destination for sweep
            const dest1 = await callToolJSON(client, 'generate_taproot_wallet', {
                network: 'regtest', backup_key: true, open_preview: false,
            });
            generatedFiles.push(dest1.bill_image, dest1.metadata_json);
            const addr2 = dest1.address;

            // Sweep (key-path using tweaked WIF from bill)
            const sweep = await callToolJSON(client, 'sweep_wallet', {
                wif: gen.private_key_wif,
                destination: addr2,
                fee_rate: 2,
                network: 'regtest',
            });
            assert(sweep.status === 'broadcast', `Sweep failed: ${JSON.stringify(sweep)}`);
            assert(sweep.address_type === 'taproot_tweaked', `Expected taproot_tweaked, got ${sweep.address_type}`);
            console.log(`  Swept: ${sweep.amount_sats} sats (${sweep.address_type}), txid=${sweep.txid.slice(0, 16)}...`);

            // Generate destination for recovery
            const dest2 = await callToolJSON(client, 'generate_segwit_wallet', {
                network: 'regtest', open_preview: false,
            });
            generatedFiles.push(dest2.bill_image, dest2.metadata_json);
            const addr3 = dest2.address;

            // Recover (script-path using backup key)
            const recover = await callToolJSON(client, 'recover_wallet', {
                backup_wif: dest1.backup_private_key_wif,
                internal_pubkey_hex: dest1.internal_pubkey_hex,
                destination: addr3,
                fee_rate: 2,
                network: 'regtest',
            });
            assert(recover.status === 'broadcast', `Recover failed: ${JSON.stringify(recover)}`);
            assert(recover.address_type === 'taproot_script_path', `Expected taproot_script_path, got ${recover.address_type}`);
            console.log(`  Recovered: ${recover.amount_sats} sats, txid=${recover.txid.slice(0, 16)}...`);

            // Verify fee chain: 1 BTC - sweep_fee - recover_fee = final amount
            const totalFees = sweep.fee_sats + recover.fee_sats;
            const expectedFinal = 100_000_000 - totalFees;
            assert(recover.amount_sats === expectedFinal,
                `Fee chain: expected ${expectedFinal}, got ${recover.amount_sats} ` +
                `(1 BTC - ${sweep.fee_sats} - ${recover.fee_sats})`);
            console.log(`  Fee chain: 100,000,000 - ${sweep.fee_sats} - ${recover.fee_sats} = ${expectedFinal} ✓`);

            // Verify final destination has the funds
            const balFinal = await callToolJSON(client, 'check_balance', {
                address: addr3, network: 'regtest',
            });
            assert(balFinal.balance_sats === expectedFinal,
                `Final balance ${balFinal.balance_sats} != expected ${expectedFinal}`);
            console.log(`  Verified: addr3 balance = ${balFinal.balance_sats} sats`);

            pass('Taproot+backup: generate → fund → sweep → recover → verify');
        } catch (e) { fail('Taproot+backup E2E', e.message); }

        // ── Test 3: check_all_balances on regtest ─────────────────────────

        try {
            console.log('\n--- Test 3: check_all_balances (regtest) ---');
            const r = await callToolJSON(client, 'check_all_balances', { network: 'regtest' });
            assert(typeof r.total_wallets === 'number', 'Missing total_wallets');
            assert(r.total_wallets >= 1, `Expected at least 1 regtest wallet, got ${r.total_wallets}`);
            console.log(`  Found ${r.total_wallets} regtest wallets, total: ${r.total_balance_sats} sats`);
            pass(`check_all_balances (${r.total_wallets} regtest wallets)`);
        } catch (e) { fail('check_all_balances (regtest)', e.message); }

    } finally {
        // Cleanup generated files
        for (const f of generatedFiles) {
            try { if (f) fs.unlinkSync(f); } catch (_) {}
        }

        // Disconnect MCP client
        if (client) {
            try { await client.close(); } catch (_) {}
        }

        // Stop regtest server
        if (serverProc) {
            serverProc.kill('SIGTERM');
            // Wait for clean shutdown
            await new Promise(r => setTimeout(r, 2000));
            try { serverProc.kill('SIGKILL'); } catch (_) {}
            console.log('\n  Server stopped.');
        }
    }

    console.log(`\n${'='.repeat(60)}`);
    console.log(`Results: ${passed}/${passed + failed} passed, ${failed} failed`);
    console.log('='.repeat(60));

    process.exit(failed > 0 ? 1 : 0);
}

main().catch(err => {
    console.error('Fatal:', err);
    process.exit(1);
});
