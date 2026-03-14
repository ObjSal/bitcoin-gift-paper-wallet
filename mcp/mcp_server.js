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

    // Save companion JSON only when walletData is provided (i.e. backup key present)
    let jsonPath = null;
    if (walletData) {
        jsonPath = outPath.replace(/\.png$/, '.json');
        const backup = Object.assign({ generated_at: new Date().toISOString() }, walletData);
        fs.writeFileSync(jsonPath, JSON.stringify(backup, null, 2));
    }

    return { billPath: outPath, jsonPath };
}

function openFile(filePath) {
    execFile('open', [filePath]);
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
                        enum: ['mainnet', 'testnet4'],
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
                        enum: ['mainnet', 'testnet4'],
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
        const { billPath } = await saveBill(wallet.address, wallet.private_key_wif, 'segwit', false, network, null);

        if (openPreview) openFile(billPath);

        return {
            content: [{
                type: 'text',
                text: JSON.stringify({
                    ...walletData,
                    bill_image: billPath,
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
        const walletData = {
            type:                   'Taproot P2TR',
            network,
            address:                wallet.address,
            private_key_wif:        wallet.private_key_wif,
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
        const { billPath, jsonPath } = await saveBill(wallet.address, wallet.private_key_wif, 'taproot', backupKey, network, backupKey ? walletData : null);

        if (openPreview) openFile(billPath);

        const result = {
            ...walletData,
            bill_image: billPath,
            ...(backupKey && { backup_json: jsonPath }),
            note: backupKey
                ? 'Bill and backup JSON saved. The JSON contains the backup WIF — store it securely, it is needed for script-path recovery.'
                : 'Bill opened in Preview. Fund the address, fold, and gift.',
        };

        return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
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
        const pngs     = allFiles.filter(f => f.endsWith('.png'));
        const wallets  = pngs.map(png => {
            const base = png.replace(/\.png$/, '');
            const json = base + '.json';
            return {
                bill: png,
                backup_json: allFiles.includes(json) ? json : null,
            };
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
