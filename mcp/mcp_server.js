#!/usr/bin/env node
/**
 * Bitcoin Gift Wallet — Node.js MCP Server
 *
 * Wraps the same JS modules used by the website (js/bitcoin_crypto.js,
 * js/qr_generator.js, js/bill_generator.js) so Claude Desktop can generate
 * paper wallets locally with zero Python dependency.
 *
 * Dependencies:
 *   @modelcontextprotocol/sdk  — MCP protocol
 *   @napi-rs/canvas             — Node.js Canvas API, prebuilt binaries (bill rendering)
 */

'use strict';

const fs   = require('fs');
const path = require('path');
const { execFile } = require('child_process');

// ── Paths ─────────────────────────────────────────────────────────────────────
const PROJECT_ROOT = path.resolve(__dirname, '..');
const JS_DIR       = path.join(PROJECT_ROOT, 'js');
const ASSETS_DIR   = path.join(PROJECT_ROOT, 'assets');

// In .mcpb bundle mode the bundle root may not be writable.
// Detect bundle mode by checking for manifest.json at project root.
const IN_BUNDLE = fs.existsSync(path.join(PROJECT_ROOT, 'manifest.json'));
const BILLS_DIR = IN_BUNDLE
    ? path.join(require('os').homedir(), 'bitcoin-gift-wallet', 'generated-bills')
    : path.join(PROJECT_ROOT, 'generated-bills');

if (!fs.existsSync(BILLS_DIR)) fs.mkdirSync(BILLS_DIR, { recursive: true });

// ── Load project JS modules ───────────────────────────────────────────────────
// bill_generator.js references QRGenerator as a global (browser pattern).
// Set it on global before requiring so the module picks it up.
global.QRGenerator   = require(path.join(JS_DIR, 'qr_generator.js'));
const BitcoinCrypto  = require(path.join(JS_DIR, 'bitcoin_crypto.js'));
const BillGenerator  = require(path.join(JS_DIR, 'bill_generator.js'));

// ── Node.js Canvas (replaces browser HTMLCanvasElement / HTMLImageElement) ────
const { createCanvas, loadImage } = require('@napi-rs/canvas');

// ── MCP SDK ───────────────────────────────────────────────────────────────────
const { Server }       = require('@modelcontextprotocol/sdk/server/index.js');
const { StdioServerTransport } = require('@modelcontextprotocol/sdk/server/stdio.js');
const {
    CallToolRequestSchema,
    ListToolsRequestSchema,
} = require('@modelcontextprotocol/sdk/types.js');

// ── Bill rendering ────────────────────────────────────────────────────────────

let _templateImg = null;   // cached after first load

async function getTemplate() {
    if (!_templateImg) {
        _templateImg = await loadImage(path.join(ASSETS_DIR, 'bill_template.png'));
    }
    return _templateImg;
}

async function saveBill(address, wif, addressType, isTweaked, network, walletData) {
    const templateImg = await getTemplate();
    const canvas      = createCanvas(BillGenerator.BILL_WIDTH, BillGenerator.BILL_HEIGHT);

    BillGenerator.generateBillOnCanvas(
        canvas, templateImg,
        address, wif,
        addressType, isTweaked, network
    );

    const ts       = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    const filename = `wallet_${addressType}_${ts}.png`;
    const outPath  = path.join(BILLS_DIR, filename);

    fs.writeFileSync(outPath, canvas.toBuffer('image/png'));

    // Always save companion JSON with wallet metadata
    const jsonPath = outPath.replace(/\.png$/, '.json');
    const metadata = Object.assign({ generated_at: new Date().toISOString() }, walletData);
    fs.writeFileSync(jsonPath, JSON.stringify(metadata, null, 2));

    return { billPath: outPath, jsonPath };
}

function openFile(filePath) {
    execFile('open', [filePath]);
}

// ── Network API helpers ──────────────────────────────────────────────────────
//
// Supports mainnet/testnet4 via mempool.space and regtest via local server.
// Set REGTEST_SERVER_URL env var to point to the local server (e.g. http://127.0.0.1:8080).

const REGTEST_SERVER_URL = process.env.REGTEST_SERVER_URL || '';

function mempoolBaseUrl(network) {
    if (network === 'testnet4') return 'https://mempool.space/testnet4/api';
    return 'https://mempool.space/api';
}

async function fetchUtxos(address, network) {
    if (network === 'regtest') {
        if (!REGTEST_SERVER_URL) throw new Error('REGTEST_SERVER_URL not set');
        const url = `${REGTEST_SERVER_URL}/api/utxos`;
        const resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ address, network: 'regtest' }),
            signal: AbortSignal.timeout(30000),
        });
        if (!resp.ok) throw new Error(`UTXO fetch failed (${resp.status}): ${await resp.text()}`);
        const data = await resp.json();
        return (data.utxos || []).map(u => ({ txid: u.txid, vout: u.vout, value_sat: u.value_sat || u.value }));
    }
    const url = `${mempoolBaseUrl(network)}/address/${address}/utxo`;
    const resp = await fetch(url, { signal: AbortSignal.timeout(30000) });
    if (!resp.ok) throw new Error(`UTXO fetch failed (${resp.status}): ${await resp.text()}`);
    const utxos = await resp.json();
    return utxos.map(u => ({ txid: u.txid, vout: u.vout, value_sat: u.value }));
}

async function broadcastTx(rawHex, network) {
    if (network === 'regtest') {
        if (!REGTEST_SERVER_URL) throw new Error('REGTEST_SERVER_URL not set');
        const url = `${REGTEST_SERVER_URL}/api/broadcast`;
        const resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ raw_hex: rawHex, network: 'regtest' }),
            signal: AbortSignal.timeout(30000),
        });
        if (!resp.ok) throw new Error(`Broadcast failed (${resp.status}): ${await resp.text()}`);
        const data = await resp.json();
        return data.txid;
    }
    const url = `${mempoolBaseUrl(network)}/tx`;
    const resp = await fetch(url, {
        method: 'POST',
        body: rawHex,
        headers: { 'Content-Type': 'text/plain' },
        signal: AbortSignal.timeout(30000),
    });
    if (!resp.ok) throw new Error(`Broadcast failed (${resp.status}): ${await resp.text()}`);
    return await resp.text(); // txid
}

async function fetchFeeRates(network) {
    if (network === 'regtest') {
        // Regtest doesn't have fee estimation; use a fixed low rate
        return { fastestFee: 2, halfHourFee: 1, hourFee: 1 };
    }
    const url = `${mempoolBaseUrl(network)}/v1/fees/recommended`;
    const resp = await fetch(url, { signal: AbortSignal.timeout(15000) });
    if (!resp.ok) throw new Error(`Fee fetch failed (${resp.status})`);
    return await resp.json();
}

function hexToBytes(hex) {
    const bytes = new Uint8Array(hex.length / 2);
    for (let i = 0; i < hex.length; i += 2) {
        bytes[i / 2] = parseInt(hex.substr(i, 2), 16);
    }
    return bytes;
}

// ── MCP server ────────────────────────────────────────────────────────────────

const server = new Server(
    { name: 'bitcoin-gift-wallet', version: '1.0.0' },
    { capabilities: { tools: {} } }
);

// ── Tool list ─────────────────────────────────────────────────────────────────

server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: [
        {
            name: 'generate_segwit_wallet',
            description:
                'Generate a Bitcoin SegWit (P2WPKH, bc1q...) paper wallet. ' +
                'Renders a gift-ready bill image and opens it in Preview. ' +
                'All keys are generated locally — nothing leaves this machine.',
            inputSchema: {
                type: 'object',
                properties: {
                    network: {
                        type: 'string',
                        enum: ['mainnet', 'testnet4', 'regtest'],
                        default: 'mainnet',
                        description: 'Bitcoin network. Use mainnet for real wallets.',
                    },
                    open_preview: {
                        type: 'boolean',
                        default: true,
                        description: 'Automatically open the bill PNG in Preview.',
                    },
                },
            },
        },
        {
            name: 'generate_taproot_wallet',
            description:
                'Generate a Bitcoin Taproot (P2TR, bc1p...) paper wallet. ' +
                'Optionally includes a backup key for script-path recovery. ' +
                'Renders a gift-ready bill image and opens it in Preview. ' +
                'All keys are generated locally — nothing leaves this machine.',
            inputSchema: {
                type: 'object',
                properties: {
                    network: {
                        type: 'string',
                        enum: ['mainnet', 'testnet4', 'regtest'],
                        default: 'mainnet',
                        description: 'Bitcoin network. Use mainnet for real wallets.',
                    },
                    backup_key: {
                        type: 'boolean',
                        default: false,
                        description:
                            'Generate a second backup key for script-path recovery. ' +
                            'The bill will show the tweaked WIF. ' +
                            'Store the backup WIF separately — needed for recovery.',
                    },
                    open_preview: {
                        type: 'boolean',
                        default: true,
                        description: 'Automatically open the bill PNG in Preview.',
                    },
                },
            },
        },
        {
            name: 'check_balance',
            description:
                'Check the Bitcoin balance of an address. ' +
                'Fetches UTXOs from mempool.space and returns the total balance in BTC and satoshis.',
            inputSchema: {
                type: 'object',
                properties: {
                    address: {
                        type: 'string',
                        description: 'Bitcoin address to check (bc1q..., bc1p..., tb1q..., tb1p...).',
                    },
                    network: {
                        type: 'string',
                        enum: ['mainnet', 'testnet4', 'regtest'],
                        default: 'mainnet',
                        description: 'Bitcoin network.',
                    },
                },
                required: ['address'],
            },
        },
        {
            name: 'check_all_balances',
            description:
                'Check balances of all previously generated wallets. ' +
                'Reads wallet metadata from generated-bills/ and fetches each balance from mempool.space. ' +
                'Use this when the user asks "how much bitcoin do I have?" or similar.',
            inputSchema: {
                type: 'object',
                properties: {
                    network: {
                        type: 'string',
                        enum: ['mainnet', 'testnet4', 'regtest'],
                        description: 'Only check wallets on this network. If omitted, checks all.',
                    },
                },
            },
        },
        {
            name: 'sweep_wallet',
            description:
                'Sweep all funds from a paper wallet to a destination address. ' +
                'Takes the private key (WIF) from the bill, fetches UTXOs, builds a signed transaction, ' +
                'and broadcasts it. Supports SegWit and Taproot (both tweaked and untweaked) keys. ' +
                'WARNING: This sends real Bitcoin — double-check the destination address.',
            inputSchema: {
                type: 'object',
                properties: {
                    wif: {
                        type: 'string',
                        description: 'Private key in WIF format (from the paper wallet bill).',
                    },
                    destination: {
                        type: 'string',
                        description: 'Destination Bitcoin address to send funds to.',
                    },
                    fee_rate: {
                        type: 'number',
                        description: 'Fee rate in sat/vB. If omitted, uses the "half hour" recommended fee.',
                    },
                    network: {
                        type: 'string',
                        enum: ['mainnet', 'testnet4', 'regtest'],
                        default: 'mainnet',
                        description: 'Bitcoin network.',
                    },
                },
                required: ['wif', 'destination'],
            },
        },
        {
            name: 'recover_wallet',
            description:
                'Recover funds from a Taproot paper wallet using the backup key (script-path spend). ' +
                'Requires the backup private key WIF and the internal public key (both from the backup JSON). ' +
                'WARNING: This sends real Bitcoin — double-check the destination address.',
            inputSchema: {
                type: 'object',
                properties: {
                    backup_wif: {
                        type: 'string',
                        description: 'Backup private key in WIF format (from the backup JSON).',
                    },
                    internal_pubkey_hex: {
                        type: 'string',
                        description: 'Internal public key as 64-char hex string (from the backup JSON).',
                    },
                    destination: {
                        type: 'string',
                        description: 'Destination Bitcoin address to send recovered funds to.',
                    },
                    fee_rate: {
                        type: 'number',
                        description: 'Fee rate in sat/vB. If omitted, uses the "half hour" recommended fee.',
                    },
                    network: {
                        type: 'string',
                        enum: ['mainnet', 'testnet4', 'regtest'],
                        default: 'mainnet',
                        description: 'Bitcoin network.',
                    },
                },
                required: ['backup_wif', 'internal_pubkey_hex', 'destination'],
            },
        },
        {
            name: 'open_wallet_app',
            description:
                'Open the Bitcoin Gift Wallet web app in the browser. ' +
                'Useful for manual generation, sweeping, or backup recovery. ' +
                'All crypto runs entirely client-side — nothing leaves this machine.',
            inputSchema: {
                type: 'object',
                properties: {
                    page: {
                        type: 'string',
                        enum: ['index', 'sweep', 'recover', 'donate'],
                        default: 'index',
                        description: 'Which page to open.',
                    },
                },
            },
        },
        {
            name: 'list_generated_wallets',
            description: 'List previously generated wallet bill images.',
            inputSchema: {
                type: 'object',
                properties: {
                    open_folder: {
                        type: 'boolean',
                        default: false,
                        description: 'Open the generated-bills folder in Finder.',
                    },
                },
            },
        },
        {
            name: 'open_wallet_bill',
            description: 'Open a previously generated wallet bill image in Preview.',
            inputSchema: {
                type: 'object',
                properties: {
                    filename: {
                        type: 'string',
                        description:
                            'Filename (e.g. wallet_taproot_2025-01-01T12-00-00.png). ' +
                            'Use list_generated_wallets to see available files.',
                    },
                },
                required: ['filename'],
            },
        },
    ],
}));

// ── Tool handlers ─────────────────────────────────────────────────────────────

server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args = {} } = request.params;

    // ── generate_segwit_wallet ─────────────────────────────────────────────
    if (name === 'generate_segwit_wallet') {
        const network     = args.network      ?? 'mainnet';
        const openPreview = args.open_preview ?? true;

        const wallet   = BitcoinCrypto.generateSegwitAddress(network);
        const walletData = {
            type:            'SegWit P2WPKH',
            network,
            address:         wallet.address,
            private_key_wif: wallet.private_key_wif,
            public_key_hex:  wallet.public_key_hex,
        };
        const { billPath, jsonPath } = await saveBill(wallet.address, wallet.private_key_wif, 'segwit', false, network, walletData);

        if (openPreview) openFile(billPath);

        return {
            content: [{
                type: 'text',
                text: JSON.stringify({
                    ...walletData,
                    bill_image: billPath,
                    metadata_json: jsonPath,
                    note: 'Bill opened in Preview. Fund the address, fold, and gift. Keep the WIF secret until the recipient is ready to sweep.',
                }, null, 2),
            }],
        };
    }

    // ── generate_taproot_wallet ────────────────────────────────────────────
    if (name === 'generate_taproot_wallet') {
        const network     = args.network      ?? 'mainnet';
        const backupKey   = args.backup_key   ?? false;
        const openPreview = args.open_preview ?? true;

        const wallet   = BitcoinCrypto.generateTaprootAddress(network, backupKey);
        // For backup wallets, the bill shows the tweaked WIF (key-path spending).
        // For non-backup wallets, the bill shows the untweaked (internal) WIF.
        let billWif;
        if (backupKey) {
            const tweakedKey = hexToBytes(wallet.tweaked_private_key_hex);
            billWif = BitcoinCrypto.privateKeyToWif(tweakedKey, true, network);
        } else {
            billWif = wallet.private_key_wif;
        }

        const walletData = {
            type:                   'Taproot P2TR',
            network,
            address:                wallet.address,
            private_key_wif:        billWif,
            internal_pubkey_hex:    wallet.internal_pubkey_hex,
            output_pubkey_hex:      wallet.output_pubkey_hex,
            tweaked_private_key_hex: wallet.tweaked_private_key_hex,
            has_backup_key:         backupKey,
            ...(backupKey && {
                backup_private_key_wif: wallet.backup_private_key_wif,
                backup_pubkey_hex:      wallet.backup_pubkey_hex,
                script_tree_hash:       wallet.script_tree_hash,
            }),
        };
        const { billPath, jsonPath } = await saveBill(wallet.address, billWif, 'taproot', backupKey, network, walletData);

        if (openPreview) openFile(billPath);

        const result = {
            ...walletData,
            bill_image: billPath,
            metadata_json: jsonPath,
            note: backupKey
                ? 'Bill and backup JSON saved. The JSON contains the backup WIF — store it securely, it is needed for script-path recovery.'
                : 'Bill opened in Preview. Fund the address, fold, and gift.',
        };

        return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
    }

    // ── check_balance ──────────────────────────────────────────────────────
    if (name === 'check_balance') {
        const address = args.address;
        const network = args.network ?? 'mainnet';

        const utxos    = await fetchUtxos(address, network);
        const totalSat = utxos.reduce((sum, u) => sum + u.value_sat, 0);
        const totalBtc = totalSat / 1e8;

        return {
            content: [{
                type: 'text',
                text: JSON.stringify({
                    address,
                    network,
                    balance_btc: totalBtc,
                    balance_sats: totalSat,
                    utxo_count: utxos.length,
                    utxos,
                }, null, 2),
            }],
        };
    }

    // ── check_all_balances ─────────────────────────────────────────────────
    if (name === 'check_all_balances') {
        const filterNetwork = args.network ?? null;
        const allFiles = fs.readdirSync(BILLS_DIR);
        const jsons    = allFiles.filter(f => f.endsWith('.json')).sort().reverse();

        const wallets = [];
        let grandTotalSat = 0;

        for (const jsonFile of jsons) {
            try {
                const data = JSON.parse(fs.readFileSync(path.join(BILLS_DIR, jsonFile), 'utf8'));
                if (!data.address || !data.network) continue;
                if (filterNetwork && data.network !== filterNetwork) continue;

                const utxos    = await fetchUtxos(data.address, data.network);
                const totalSat = utxos.reduce((sum, u) => sum + u.value_sat, 0);
                grandTotalSat += totalSat;

                wallets.push({
                    file: jsonFile,
                    type: data.type,
                    network: data.network,
                    address: data.address,
                    balance_btc: totalSat / 1e8,
                    balance_sats: totalSat,
                    utxo_count: utxos.length,
                });
            } catch (_) { /* skip unreadable files */ }
        }

        return {
            content: [{
                type: 'text',
                text: JSON.stringify({
                    total_wallets: wallets.length,
                    total_balance_btc: grandTotalSat / 1e8,
                    total_balance_sats: grandTotalSat,
                    wallets,
                }, null, 2),
            }],
        };
    }

    // ── sweep_wallet ───────────────────────────────────────────────────────
    if (name === 'sweep_wallet') {
        const wifStr      = args.wif;
        const destination = args.destination;
        const network     = args.network ?? 'mainnet';

        // Decode WIF to get raw private key
        const decoded = BitcoinCrypto.wifToPrivateKey(wifStr);
        const privkeyBytes = decoded.privateKey;

        // Determine address type by trying SegWit first, then Taproot
        // SegWit: derive pubkey → hash160 → P2WPKH address
        const segwit = BitcoinCrypto.deriveSegwitAddressFromPrivkey(privkeyBytes, network);

        // Taproot tweaked: derive output key directly from tweaked privkey
        const taprootTweaked = BitcoinCrypto.deriveTaprootAddressFromTweakedPrivkey(privkeyBytes, network);

        // Taproot untweaked (BIP86): tweak with null merkle root then derive
        const tweakedKey = BitcoinCrypto.taprootTweakSeckey(privkeyBytes, null);
        const taprootUntweaked = BitcoinCrypto.deriveTaprootAddressFromTweakedPrivkey(tweakedKey, network);

        // Fetch UTXOs for each possible address to find which one has funds
        let address, addressType, signingKey, inputScriptpubkey;

        for (const candidate of [
            { addr: segwit.address, type: 'segwit', key: privkeyBytes, sp: segwit.scriptpubkey },
            { addr: taprootTweaked.address, type: 'taproot_tweaked', key: privkeyBytes, sp: taprootTweaked.scriptpubkey },
            { addr: taprootUntweaked.address, type: 'taproot_untweaked', key: tweakedKey, sp: taprootUntweaked.scriptpubkey },
        ]) {
            const utxos = await fetchUtxos(candidate.addr, network);
            if (utxos.length > 0) {
                address = candidate.addr;
                addressType = candidate.type;
                signingKey = candidate.key;
                inputScriptpubkey = candidate.sp;
                break;
            }
        }

        if (!address) {
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify({
                        error: 'No funds found',
                        checked_addresses: {
                            segwit: segwit.address,
                            taproot_tweaked: taprootTweaked.address,
                            taproot_untweaked: taprootUntweaked.address,
                        },
                        note: 'None of the derived addresses have any UTXOs.',
                    }, null, 2),
                }],
            };
        }

        // Re-fetch UTXOs for the address with funds
        const utxos    = await fetchUtxos(address, network);
        const totalSat = utxos.reduce((sum, u) => sum + u.value_sat, 0);

        // Get fee rate
        let feeRate = args.fee_rate;
        if (!feeRate) {
            const fees = await fetchFeeRates(network);
            feeRate = fees.halfHourFee;
        }

        // Estimate vsize and fee
        const nInputs = utxos.length;
        let vsize;
        if (addressType === 'segwit') {
            vsize = 11 + nInputs * 69 + 31;
        } else {
            vsize = 11 + nInputs * 58 + 43;
        }
        const feeSat     = Math.ceil(vsize * feeRate);
        const sendSat    = totalSat - feeSat;

        if (sendSat <= 0) {
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify({
                        error: 'Insufficient funds',
                        balance_sats: totalSat,
                        estimated_fee_sats: feeSat,
                        fee_rate: feeRate,
                    }, null, 2),
                }],
            };
        }

        // Build and sign transaction
        let rawHex;
        if (addressType === 'segwit') {
            rawHex = BitcoinCrypto.buildSignedSegwitSweepTx(
                signingKey, utxos, destination, sendSat
            );
        } else {
            rawHex = BitcoinCrypto.buildSignedTaprootSweepTx(
                signingKey, utxos, inputScriptpubkey,
                destination, sendSat
            );
        }

        // Broadcast
        const txid = await broadcastTx(rawHex, network);

        return {
            content: [{
                type: 'text',
                text: JSON.stringify({
                    status: 'broadcast',
                    txid,
                    from_address: address,
                    address_type: addressType,
                    to_address: destination,
                    amount_sats: sendSat,
                    amount_btc: sendSat / 1e8,
                    fee_sats: feeSat,
                    fee_rate_sat_vb: feeRate,
                    explorer_url: network === 'regtest' ? `regtest:${txid}`
                        : network === 'testnet4'
                        ? `https://mempool.space/testnet4/tx/${txid}`
                        : `https://mempool.space/tx/${txid}`,
                }, null, 2),
            }],
        };
    }

    // ── recover_wallet ─────────────────────────────────────────────────────
    if (name === 'recover_wallet') {
        const backupWif        = args.backup_wif;
        const internalPubHex   = args.internal_pubkey_hex;
        const destination      = args.destination;
        const network          = args.network ?? 'mainnet';

        // Decode backup WIF
        const decoded          = BitcoinCrypto.wifToPrivateKey(backupWif);
        const backupPrivBytes  = decoded.privateKey;

        // Derive backup public key (x-only)
        const { xOnly: backupPubX } = BitcoinCrypto.privateKeyToXonlyPubkey(backupPrivBytes);

        // Parse internal pubkey
        const internalPubX     = hexToBytes(internalPubHex);

        // Compute script tree hash and tweaked output key
        const scriptTreeHash   = BitcoinCrypto.computeScriptTreeHashForBackup(backupPubX);
        const { outputKeyX, parity } = BitcoinCrypto.taprootTweakPubkey(internalPubX, scriptTreeHash);

        // Derive the address
        const hrp = network === 'regtest' ? 'bcrt' : network === 'testnet4' ? 'tb' : 'bc';
        const address = BitcoinCrypto.bech32Encode(hrp, 1, Array.from(outputKeyX), 'bech32m');
        const inputScriptpubkey = BitcoinCrypto._addressToScriptpubkey(address);

        // Fetch UTXOs
        const utxos    = await fetchUtxos(address, network);
        const totalSat = utxos.reduce((sum, u) => sum + u.value_sat, 0);

        if (utxos.length === 0) {
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify({
                        error: 'No funds found',
                        address,
                        note: 'The reconstructed address has no UTXOs.',
                    }, null, 2),
                }],
            };
        }

        // Get fee rate
        let feeRate = args.fee_rate;
        if (!feeRate) {
            const fees = await fetchFeeRates(network);
            feeRate = fees.halfHourFee;
        }

        // Estimate vsize (script-path)
        const nInputs = utxos.length;
        const vsize   = 11 + nInputs * 107 + 43;
        const feeSat  = Math.ceil(vsize * feeRate);
        const sendSat = totalSat - feeSat;

        if (sendSat <= 0) {
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify({
                        error: 'Insufficient funds',
                        balance_sats: totalSat,
                        estimated_fee_sats: feeSat,
                        fee_rate: feeRate,
                    }, null, 2),
                }],
            };
        }

        // Build and sign script-path transaction
        const rawHex = BitcoinCrypto.buildSignedTaprootScriptpathSweepTx(
            backupPrivBytes, backupPubX,
            internalPubX, parity,
            utxos, inputScriptpubkey,
            destination, sendSat
        );

        // Broadcast
        const txid = await broadcastTx(rawHex, network);

        return {
            content: [{
                type: 'text',
                text: JSON.stringify({
                    status: 'broadcast',
                    txid,
                    from_address: address,
                    address_type: 'taproot_script_path',
                    to_address: destination,
                    amount_sats: sendSat,
                    amount_btc: sendSat / 1e8,
                    fee_sats: feeSat,
                    fee_rate_sat_vb: feeRate,
                    explorer_url: network === 'regtest' ? `regtest:${txid}`
                        : network === 'testnet4'
                        ? `https://mempool.space/testnet4/tx/${txid}`
                        : `https://mempool.space/tx/${txid}`,
                }, null, 2),
            }],
        };
    }

    // ── open_wallet_app ────────────────────────────────────────────────────
    if (name === 'open_wallet_app') {
        const pageMap = { index: 'index.html', sweep: 'sweep.html', recover: 'recover.html', donate: 'donate.html' };
        const page    = args.page ?? 'index';
        const filePath = path.join(PROJECT_ROOT, pageMap[page] ?? 'index.html');
        openFile(filePath);
        return {
            content: [{
                type: 'text',
                text: JSON.stringify({
                    status: 'opened',
                    page,
                    path: filePath,
                    note: 'All crypto runs client-side in your browser — nothing leaves this machine.',
                }, null, 2),
            }],
        };
    }

    // ── list_generated_wallets ─────────────────────────────────────────────
    if (name === 'list_generated_wallets') {
        const allFiles = fs.readdirSync(BILLS_DIR).sort().reverse();
        const jsons    = allFiles.filter(f => f.endsWith('.json'));
        const wallets  = jsons.map(jsonFile => {
            try {
                const data = JSON.parse(fs.readFileSync(path.join(BILLS_DIR, jsonFile), 'utf8'));
                const bill = jsonFile.replace(/\.json$/, '.png');
                return {
                    bill: allFiles.includes(bill) ? bill : null,
                    metadata_json: jsonFile,
                    type: data.type || null,
                    network: data.network || null,
                    address: data.address || null,
                    has_backup_key: data.has_backup_key || false,
                };
            } catch (_) {
                return { metadata_json: jsonFile };
            }
        });

        if (args.open_folder) openFile(BILLS_DIR);

        return {
            content: [{
                type: 'text',
                text: JSON.stringify({ directory: BILLS_DIR, count: wallets.length, wallets }, null, 2),
            }],
        };
    }

    // ── open_wallet_bill ───────────────────────────────────────────────────
    if (name === 'open_wallet_bill') {
        const filePath = path.join(BILLS_DIR, args.filename ?? '');
        if (!fs.existsSync(filePath)) {
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify({ error: `File not found: ${args.filename}. Use list_generated_wallets to see available files.` }),
                }],
            };
        }
        openFile(filePath);
        return { content: [{ type: 'text', text: JSON.stringify({ status: 'opened', path: filePath }, null, 2) }] };
    }

    return { content: [{ type: 'text', text: JSON.stringify({ error: `Unknown tool: ${name}` }) }] };
});

// ── Start ─────────────────────────────────────────────────────────────────────

async function main() {
    const transport = new StdioServerTransport();
    await server.connect(transport);
}

main().catch(err => {
    process.stderr.write(`bitcoin-gift-wallet MCP error: ${err}\n`);
    process.exit(1);
});
