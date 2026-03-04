# UI Test: Chained Sweep → Recovery (Regtest)

## Overview
End-to-end UI test that drives the full fund flow through the website:
Faucet → Sweep → Recovery, across 3 Taproot+backup addresses.

**Run with:** Tell Claude Code: _"Run the UI test in test_ui_chained.md"_

## Prerequisites
- Bitcoin Core installed (`brew install bitcoin`)
- Start regtest server: `preview_start` with `bitcoin-gift-wallet-regtest`
- Wait for server to be ready (bitcoind mines 101 blocks at startup, ~15s)

## Element Reference

### Generator (`/`)
| Element | Selector | Notes |
|---------|----------|-------|
| Taproot radio | `.type-card` (right one) | Selected by default |
| Backup checkbox | `#backupCheckbox` | NOT `backupKeyCheckbox` |
| Network select | `#networkSelect` | Values: `mainnet`, `testnet4`, `regtest` |
| Generate button | `button` (only one) | Text: "Generate New Wallet" |
| Generate Another | button by text | No ID — find via `textContent === 'Generate Another'` |
| Wallet details | `#walletDetails` | `.innerText` has all keys/addresses |

### Faucet (`/faucet.html`)
| Element | Selector | Notes |
|---------|----------|-------|
| Address input | `#addressInput` | NOT `faucetAddress` |
| Amount input | `#amountInput` | Default: 1.0 |
| Fund button | `#fundBtn` | |

### Sweep (`/sweep.html`)
| Element | Selector | Notes |
|---------|----------|-------|
| WIF input | `#wifInput` | |
| SegWit radio | `input[name="addrType"][value="segwit"]` | |
| Taproot standard radio | `input[name="addrType"][value="taproot"]` | |
| Taproot tweaked radio | `input[name="addrType"][value="taproot_tweaked"]` | |
| Network select | `#networkSelect` | Dispatch `change` event after setting |
| Derive button | `#btnDerive` | NOT `deriveBtn` |
| Derived address | `#derivedAddress` | `.innerText` for verification |
| Check Balance button | `#btnCheckBalance` | |
| Destination input | `#destAddress` | |
| Fee rate input | `#feeRate` | Dispatch `input` event after setting |
| Sweep button | `#btnSweep` | |

### Recovery (`/recover.html`)
| Element | Selector | Notes |
|---------|----------|-------|
| Backup WIF input | `#backupWifInput` | |
| Internal pubkey input | `#internalPubkeyInput` | 64-char hex |
| Network select | `#networkSelect` | Dispatch `change` event after setting |
| Reconstruct button | `#btnDerive` | Text: "Reconstruct Address" |
| Derived address | `#derivedAddress` | `.innerText` for verification |
| Check Balance button | `#btnCheckBalance` | |
| Destination input | `#destAddress` | |
| Fee rate input | `#feeRate` | Dispatch `input` event after setting |
| Recover button | `#btnRecover` | |

## Implementation Notes

- **Use `preview_eval`** to set form values (more reliable than `preview_fill` for these pages):
  ```javascript
  document.getElementById('wifInput').value = '...';
  document.getElementById('networkSelect').value = 'regtest';
  document.getElementById('networkSelect').dispatchEvent(new Event('change'));
  ```
- **Use `preview_click`** with `#id` selectors for buttons.
- **Wait 2-3s** after button clicks before reading results (crypto operations + API calls).
- **Wallet details parsing:** `#walletDetails` `.innerText` contains labeled fields separated by newlines. Parse with regex or split by known labels (ADDRESS, TWEAKED PRIVATE KEY, BACKUP PRIVATE KEY, INTERNAL PUBLIC KEY, etc.).

## Test Steps

### Step 1: Generate Address 1 (Taproot + Backup)
- **Page:** Generator (`/`)
- **Actions:**
  1. `#backupCheckbox` — click to enable backup key
  2. `#networkSelect` — set to `regtest`, dispatch `change` event
  3. Click `button` (Generate New Wallet) — Taproot is already selected by default
- **Wait** 3s for key generation + bill rendering
- **Capture from `#walletDetails`:** `address_1` (bcrt1p...), `bill_wif_1` (TWEAKED PRIVATE KEY WIF)

### Step 2: Generate Address 2 (Taproot + Backup)
- **Page:** Same — click "Generate Another" button (find by text, no ID)
- **Wait** 3s
- **Capture from `#walletDetails`:** `address_2`, `bill_wif_2`, `backup_wif_2` (BACKUP PRIVATE KEY WIF), `internal_pubkey_2` (INTERNAL PUBLIC KEY)

### Step 3: Generate Address 3 (Taproot + Backup)
- **Page:** Same — click "Generate Another" button
- **Wait** 3s
- **Capture from `#walletDetails`:** `address_3`

### Step 4: Fund Address 1 via Faucet
- **Page:** Faucet (`/faucet.html`)
- **Actions:**
  1. `#addressInput` — set to `address_1`
  2. Leave `#amountInput` as 1.0
  3. Click `#fundBtn`
- **Wait** 3s
- **Verify:** "Funded Successfully" message visible with 100,000,000 sats

### Step 5: Sweep Address 1 → Address 2
- **Page:** Sweep (`/sweep.html`)
- **Actions (Step 1):**
  1. `#wifInput` — set to `bill_wif_1`
  2. `input[name="addrType"][value="taproot_tweaked"]` — check
  3. `#networkSelect` — set to `regtest`, dispatch `change`
  4. Click `#btnDerive`
- **Wait** 2s
- **Verify:** `#derivedAddress` text matches `address_1`
- **Actions (Step 2):**
  5. Click `#btnCheckBalance`
- **Wait** 3s — balance loads (100,000,000 sats)
- **Actions (Step 3):**
  6. `#destAddress` — set to `address_2`
  7. Click `#btnSweep`
- **Wait** 5s (signing + broadcast + auto-mine)
- **Verify:** "Confirmed (1 block mined)" with destination = `address_2`
- **Capture:** `sweep_amount`, `sweep_fee` from result screen

### Step 6: Recover Address 2 → Address 3
- **Page:** Recovery (`/recover.html`)
- **Actions (Step 1):**
  1. `#backupWifInput` — set to `backup_wif_2`
  2. `#internalPubkeyInput` — set to `internal_pubkey_2`
  3. `#networkSelect` — set to `regtest`, dispatch `change`
  4. Click `#btnDerive`
- **Wait** 2s
- **Verify:** `#derivedAddress` text matches `address_2`
- **Actions (Step 2):**
  5. Click `#btnCheckBalance`
- **Wait** 3s — balance loads (should match `sweep_amount`)
- **Actions (Step 3):**
  6. `#destAddress` — set to `address_3`
  7. Click `#btnRecover`
- **Wait** 5s (signing + broadcast + auto-mine)
- **Verify:** "Confirmed (1 block mined)" with destination = `address_3`
- **Capture:** `recover_amount`, `recover_fee` from result screen

### Step 7: Verify Fee Chain
- **Check:** `sweep_fee + recover_fee` = total deducted from 1.0 BTC
- **Check:** `recover_amount` = 100,000,000 - sweep_fee - recover_fee
- **Report:** Final summary table

## Expected Result

| Step | From | To | Amount | Fee |
|------|------|----|--------|-----|
| Faucet | coinbase | Address 1 | 1.0 BTC (100,000,000 sats) | — |
| Sweep | Address 1 | Address 2 | ~99,998,880 sats | ~1,120 sats (at 10 sat/vB) |
| Recover | Address 2 | Address 3 | ~99,997,270 sats | ~1,610 sats (at 10 sat/vB) |

All transactions confirmed on-chain. Fee depends on the default fee rate (10 sat/vB).
Sweep uses key-path (112 vB), recovery uses script-path (161 vB).
