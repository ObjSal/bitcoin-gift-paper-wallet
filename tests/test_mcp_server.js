#!/usr/bin/env node
/**
 * Tests for the Node.js MCP server.
 *
 * Uses the MCP SDK client to connect to the server over stdio and exercise
 * all 9 tools: generate_segwit_wallet, generate_taproot_wallet,
 * check_balance, check_all_balances, sweep_wallet, recover_wallet,
 * list_generated_wallets, open_wallet_bill, open_wallet_app.
 *
 * Usage:
 *     node tests/test_mcp_server.js
 */

'use strict';

const fs   = require('fs');
const path = require('path');

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

async function main() {
    console.log('============================================================');
    console.log('Testing Node.js MCP server');
    console.log('============================================================');

    const transport = new StdioClientTransport({
        command: 'node',
        args: [SERVER_PATH],
        cwd: PROJECT_ROOT,
    });
    const client = new Client({ name: 'test', version: '1.0.0' });
    await client.connect(transport);

    // Track generated files for cleanup
    const generatedFiles = [];

    // ── test_list_tools ──────────────────────────────────────────────────
    try {
        const { tools } = await client.listTools();
        const names = new Set(tools.map(t => t.name));
        const expected = ['generate_segwit_wallet', 'generate_taproot_wallet',
                          'check_balance', 'check_all_balances',
                          'sweep_wallet', 'recover_wallet',
                          'open_wallet_app', 'list_generated_wallets', 'open_wallet_bill'];
        for (const e of expected) {
            assert(names.has(e), `Missing tool: ${e}`);
        }
        assert(names.size === expected.length, `Expected ${expected.length} tools, got ${names.size}`);
        pass(`list_tools returns all ${expected.length} tools`);
    } catch (e) { fail('list_tools', e.message); }

    // ── test_generate_segwit_mainnet ─────────────────────────────────────
    let segwitBill = null;
    let segwitAddress = null;
    try {
        const r = await callToolJSON(client, 'generate_segwit_wallet', {
            network: 'mainnet', open_preview: false,
        });
        assert(r.type === 'SegWit P2WPKH', `Wrong type: ${r.type}`);
        assert(r.address.startsWith('bc1q'), `Bad address: ${r.address}`);
        assert(r.private_key_wif.startsWith('K') || r.private_key_wif.startsWith('L'),
               `Bad WIF prefix: ${r.private_key_wif[0]}`);
        assert(fs.existsSync(r.bill_image), `Bill not found: ${r.bill_image}`);
        const magic = fs.readFileSync(r.bill_image).slice(0, 4);
        assert(magic[0] === 0x89 && magic[1] === 0x50, 'Not a valid PNG');
        // Verify metadata JSON is always saved
        assert(r.metadata_json && fs.existsSync(r.metadata_json), 'Metadata JSON not found');
        const meta = JSON.parse(fs.readFileSync(r.metadata_json, 'utf8'));
        assert(meta.address === r.address, 'Metadata address mismatch');
        assert(meta.network === 'mainnet', 'Metadata network mismatch');
        segwitBill = r.bill_image;
        segwitAddress = r.address;
        generatedFiles.push(r.bill_image, r.metadata_json);
        pass('generate_segwit_wallet (mainnet + metadata JSON)');
    } catch (e) { fail('generate_segwit_wallet (mainnet)', e.message); }

    // ── test_generate_segwit_testnet4 ────────────────────────────────────
    try {
        const r = await callToolJSON(client, 'generate_segwit_wallet', {
            network: 'testnet4', open_preview: false,
        });
        assert(r.address.startsWith('tb1q'), `Bad address: ${r.address}`);
        assert(r.private_key_wif.startsWith('c'), `Bad WIF: ${r.private_key_wif[0]}`);
        assert(fs.existsSync(r.bill_image), 'Bill not found');
        assert(r.metadata_json && fs.existsSync(r.metadata_json), 'Metadata JSON not found');
        generatedFiles.push(r.bill_image, r.metadata_json);
        pass('generate_segwit_wallet (testnet4)');
    } catch (e) { fail('generate_segwit_wallet (testnet4)', e.message); }

    // ── test_generate_taproot_no_backup ──────────────────────────────────
    try {
        const r = await callToolJSON(client, 'generate_taproot_wallet', {
            network: 'mainnet', backup_key: false, open_preview: false,
        });
        assert(r.type === 'Taproot P2TR', `Wrong type: ${r.type}`);
        assert(r.address.startsWith('bc1p'), `Bad address: ${r.address}`);
        assert(r.has_backup_key === false, 'Should not have backup key');
        assert(!r.backup_private_key_wif, 'Should not have backup WIF');
        assert(fs.existsSync(r.bill_image), 'Bill not found');
        assert(r.metadata_json && fs.existsSync(r.metadata_json), 'Metadata JSON not found');
        generatedFiles.push(r.bill_image, r.metadata_json);
        pass('generate_taproot_wallet (no backup + metadata JSON)');
    } catch (e) { fail('generate_taproot_wallet (no backup)', e.message); }

    // ── test_generate_taproot_with_backup ────────────────────────────────
    try {
        const r = await callToolJSON(client, 'generate_taproot_wallet', {
            network: 'mainnet', backup_key: true, open_preview: false,
        });
        assert(r.address.startsWith('bc1p'), `Bad address: ${r.address}`);
        assert(r.has_backup_key === true, 'Should have backup key');
        assert(r.backup_private_key_wif, 'Missing backup WIF');
        assert(r.backup_private_key_wif.startsWith('K') || r.backup_private_key_wif.startsWith('L'),
               `Bad backup WIF prefix: ${r.backup_private_key_wif[0]}`);
        assert(r.script_tree_hash, 'Missing script_tree_hash');
        assert(fs.existsSync(r.bill_image), 'Bill not found');
        assert(r.metadata_json && fs.existsSync(r.metadata_json), 'Metadata JSON not found');
        // Validate metadata JSON content
        const meta = JSON.parse(fs.readFileSync(r.metadata_json, 'utf8'));
        assert(meta.backup_private_key_wif === r.backup_private_key_wif, 'Metadata backup WIF mismatch');
        assert(meta.internal_pubkey_hex, 'Metadata missing internal_pubkey_hex');
        generatedFiles.push(r.bill_image, r.metadata_json);
        pass('generate_taproot_wallet (with backup)');
    } catch (e) { fail('generate_taproot_wallet (with backup)', e.message); }

    // ── test_generate_taproot_testnet4 ───────────────────────────────────
    try {
        const r = await callToolJSON(client, 'generate_taproot_wallet', {
            network: 'testnet4', open_preview: false,
        });
        assert(r.address.startsWith('tb1p'), `Bad address: ${r.address}`);
        assert(fs.existsSync(r.bill_image), 'Bill not found');
        generatedFiles.push(r.bill_image, r.metadata_json);
        pass('generate_taproot_wallet (testnet4)');
    } catch (e) { fail('generate_taproot_wallet (testnet4)', e.message); }

    // ── test_default_parameters ──────────────────────────────────────────
    try {
        const r = await callToolJSON(client, 'generate_segwit_wallet', {});
        assert(r.address.startsWith('bc1q'), `Default should be mainnet: ${r.address}`);
        assert(fs.existsSync(r.bill_image), 'Bill not found');
        generatedFiles.push(r.bill_image, r.metadata_json);
        pass('default parameters (empty args → mainnet segwit)');
    } catch (e) { fail('default parameters', e.message); }

    // ── test_check_balance ───────────────────────────────────────────────
    if (segwitAddress) {
        try {
            const r = await callToolJSON(client, 'check_balance', {
                address: segwitAddress, network: 'mainnet',
            });
            assert(r.address === segwitAddress, 'Address mismatch');
            assert(r.network === 'mainnet', 'Network mismatch');
            assert(typeof r.balance_btc === 'number', 'Missing balance_btc');
            assert(typeof r.balance_sats === 'number', 'Missing balance_sats');
            assert(typeof r.utxo_count === 'number', 'Missing utxo_count');
            assert(Array.isArray(r.utxos), 'utxos should be array');
            // Fresh wallet should have 0 balance
            assert(r.balance_sats === 0, `Fresh wallet should have 0 balance, got ${r.balance_sats}`);
            pass('check_balance (fresh wallet → 0 sats)');
        } catch (e) { fail('check_balance', e.message); }
    }

    // ── test_check_all_balances ──────────────────────────────────────────
    try {
        const r = await callToolJSON(client, 'check_all_balances', { network: 'mainnet' });
        assert(typeof r.total_wallets === 'number', 'Missing total_wallets');
        assert(typeof r.total_balance_btc === 'number', 'Missing total_balance_btc');
        assert(typeof r.total_balance_sats === 'number', 'Missing total_balance_sats');
        assert(Array.isArray(r.wallets), 'wallets should be array');
        assert(r.total_wallets >= 1, 'Should have at least 1 wallet');
        // Each wallet entry should have required fields
        const w = r.wallets[0];
        assert(w.address, 'Wallet missing address');
        assert(w.network, 'Wallet missing network');
        assert(typeof w.balance_sats === 'number', 'Wallet missing balance_sats');
        pass(`check_all_balances (${r.total_wallets} mainnet wallets)`);
    } catch (e) { fail('check_all_balances', e.message); }

    // ── test_sweep_wallet_no_funds ───────────────────────────────────────
    try {
        // Generate a fresh wallet and try to sweep — should get "No funds found"
        const gen = await callToolJSON(client, 'generate_segwit_wallet', {
            network: 'mainnet', open_preview: false,
        });
        generatedFiles.push(gen.bill_image, gen.metadata_json);

        const r = await callToolJSON(client, 'sweep_wallet', {
            wif: gen.private_key_wif,
            destination: 'bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4',
            network: 'mainnet',
        });
        assert(r.error === 'No funds found', `Expected 'No funds found', got: ${r.error}`);
        assert(r.checked_addresses, 'Missing checked_addresses');
        assert(r.checked_addresses.segwit, 'Missing segwit address');
        pass('sweep_wallet (no funds → error)');
    } catch (e) { fail('sweep_wallet (no funds)', e.message); }

    // ── test_recover_wallet_no_funds ─────────────────────────────────────
    try {
        // Generate a taproot+backup wallet and try to recover — should get "No funds found"
        const gen = await callToolJSON(client, 'generate_taproot_wallet', {
            network: 'mainnet', backup_key: true, open_preview: false,
        });
        generatedFiles.push(gen.bill_image, gen.metadata_json);

        const r = await callToolJSON(client, 'recover_wallet', {
            backup_wif: gen.backup_private_key_wif,
            internal_pubkey_hex: gen.internal_pubkey_hex,
            destination: 'bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4',
            network: 'mainnet',
        });
        assert(r.error === 'No funds found', `Expected 'No funds found', got: ${r.error}`);
        assert(r.address, 'Missing reconstructed address');
        // Verify the reconstructed address matches the generated one
        assert(r.address === gen.address, `Address mismatch: ${r.address} vs ${gen.address}`);
        pass('recover_wallet (no funds → error, address matches)');
    } catch (e) { fail('recover_wallet (no funds)', e.message); }

    // ── test_list_generated_wallets ──────────────────────────────────────
    try {
        const r = await callToolJSON(client, 'list_generated_wallets', { open_folder: false });
        assert(typeof r.count === 'number', `Missing count`);
        assert(r.count >= 1, 'Should have at least 1 wallet');
        assert(Array.isArray(r.wallets), 'wallets should be array');
        // Verify rich metadata in list output
        const w = r.wallets[0];
        assert(w.metadata_json, 'Missing metadata_json');
        assert(w.address, 'Missing address in list');
        assert(w.type, 'Missing type in list');
        assert(w.network, 'Missing network in list');
        pass(`list_generated_wallets (${r.count} wallets with metadata)`);
    } catch (e) { fail('list_generated_wallets', e.message); }

    // ── test_open_wallet_bill_not_found ──────────────────────────────────
    try {
        const r = await callToolJSON(client, 'open_wallet_bill', { filename: 'nonexistent.png' });
        assert(r.error, `Expected error, got: ${JSON.stringify(r)}`);
        pass('open_wallet_bill (not found → error)');
    } catch (e) { fail('open_wallet_bill (not found)', e.message); }

    // ── test_open_wallet_bill_exists ─────────────────────────────────────
    if (segwitBill) {
        try {
            const basename = path.basename(segwitBill);
            const r = await callToolJSON(client, 'open_wallet_bill', { filename: basename });
            assert(r.status === 'opened', `Expected opened, got: ${JSON.stringify(r)}`);
            pass('open_wallet_bill (existing file)');
        } catch (e) { fail('open_wallet_bill (existing file)', e.message); }
    }

    // ── test_open_wallet_app ─────────────────────────────────────────────
    try {
        for (const page of ['index', 'sweep', 'recover', 'donate']) {
            const r = await callToolJSON(client, 'open_wallet_app', { page });
            assert(r.status === 'opened', `Failed to open ${page}`);
            assert(r.page === page, `Wrong page: ${r.page}`);
        }
        pass('open_wallet_app (all 4 pages)');
    } catch (e) { fail('open_wallet_app', e.message); }

    // ── test_address_uniqueness ──────────────────────────────────────────
    try {
        const addresses = new Set();
        for (let i = 0; i < 5; i++) {
            const r = await callToolJSON(client, 'generate_segwit_wallet', {
                network: 'mainnet', open_preview: false,
            });
            assert(!addresses.has(r.address), `Duplicate address: ${r.address}`);
            addresses.add(r.address);
            generatedFiles.push(r.bill_image, r.metadata_json);
        }
        pass('address uniqueness (5 consecutive wallets)');
    } catch (e) { fail('address uniqueness', e.message); }

    // ── Cleanup generated test files ─────────────────────────────────────
    for (const f of generatedFiles) {
        try { fs.unlinkSync(f); } catch (_) {}
    }

    await client.close();

    console.log(`\n${'='.repeat(60)}`);
    console.log(`Results: ${passed}/${passed + failed} passed, ${failed} failed`);
    console.log('='.repeat(60));

    process.exit(failed > 0 ? 1 : 0);
}

main().catch(err => {
    console.error('Fatal:', err);
    process.exit(1);
});
