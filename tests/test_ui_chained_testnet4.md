# UI Test: Testnet4 Sweep → Return (via Mempool.space)

## Overview
End-to-end UI test on Bitcoin Testnet4 using mempool.space as the backend.
Sweeps a pre-funded address to a freshly generated address, then sweeps back
to return the funds. No faucet step — requires a pre-funded Testnet4 address.

Supports both **SegWit** (`tb1q...`) and **Taproot** (`tb1p...`) funded addresses.
The sweep page address type is selected based on the funded address prefix.

**Run with:** Tell Claude Code: _"Run the UI test in test_ui_chained_testnet4.md"_

## Prerequisites
- Start server: `preview_start` with `bitcoin-gift-wallet`
- A pre-funded Testnet4 address with some tBTC (e.g., funded via https://mempool.space/testnet4/faucet)
- The WIF private key for that address
- Environment variables `TESTNET4_WIF` and `TESTNET4_ADDRESS` are set in `.claude/settings.local.json`

## Preparation

Before running this test, you need a funded Testnet4 address. Two options:

### Option A: SegWit address (simpler)
1. Generate a SegWit address externally or via the Generator
2. Fund it via https://mempool.space/testnet4/faucet
3. Wait for confirmation on https://mempool.space/testnet4
4. Save the **Private Key (WIF)** — this is `funded_wif`
5. Save the **Address** (starts with `tb1q...`) — this is `funded_address`

### Option B: Taproot + Backup address
1. Go to Generator (`/`)
2. Select **Taproot (P2TR)**, check **Enable Recovery Backup Key**, select **Testnet4**
3. Click **Generate New Wallet**
4. Copy the **Address** (starts with `tb1p...`)
5. Fund it via https://mempool.space/testnet4/faucet or any testnet4 faucet
6. Wait for confirmation on https://mempool.space/testnet4
7. Save the **Tweaked Private Key (WIF)** — this is `funded_wif`
8. Save the **Address** — this is `funded_address`

## Element Reference

### Generator (`/`)
| Element | Selector | Notes |
|---------|----------|-------|
| Taproot radio | `.type-card` (right one) | Selected by default |
| Backup checkbox | `#backupCheckbox` | NOT `backupKeyCheckbox` |
| Network select | `#networkSelect` | Values: `mainnet`, `testnet4`, `regtest` |
| Generate button | `button` (only one) | Text: "Generate New Wallet" |
| Wallet details | `#walletDetails` | `.innerText` has all keys/addresses |

### Sweep (`/sweep.html`)
| Element | Selector | Notes |
|---------|----------|-------|
| WIF input | `#wifInput` | |
| SegWit radio | `input[name="addrType"][value="segwit"]` | |
| Taproot tweaked radio | `input[name="addrType"][value="taproot_tweaked"]` | |
| Network select | `#networkSelect` | Dispatch `change` event after setting |
| Derive button | `#btnDerive` | NOT `deriveBtn` |
| Derived address | `#derivedAddress` | `.innerText` for verification |
| Check Balance button | `#btnCheckBalance` | |
| Destination input | `#destAddress` | |
| Fee rate input | `#feeRate` | Dispatch `input` event after setting |
| Sweep button | `#btnSweep` | |

## Implementation Notes

- **Use `preview_eval`** to set form values (more reliable than `preview_fill`):
  ```javascript
  document.getElementById('wifInput').value = '...';
  document.getElementById('networkSelect').value = 'testnet4';
  document.getElementById('networkSelect').dispatchEvent(new Event('change'));
  ```
- **Use `preview_click`** with `#id` selectors for buttons.
- **Wait 3-5s** after sweep button clicks (mempool.space API calls are slower than regtest).
- **Wallet details parsing:** `#walletDetails` `.innerText` contains labeled fields separated by newlines.
- **Fee rate:** Set `#feeRate` to `2` and dispatch `input` event to update the fee estimate display.

## Test Steps

### Step 0: Pre-flight Balance Check
- **Action:** Use `WebFetch` to query `https://mempool.space/testnet4/api/address/{funded_address}/utxo`
- **Verify:** At least one confirmed UTXO exists with ≥1,000 sats total
- **If empty or insufficient:** STOP — ask the user to fund the address via https://mempool.space/testnet4/faucet
- **Report:** Balance in sats and number of UTXOs

### Step 1: Generate a Destination Address (Taproot + Backup)
- **Page:** Generator (`/`)
- **Actions:**
  1. `#backupCheckbox` — click to enable backup key (Taproot is already selected by default)
  2. `#networkSelect` — set to `testnet4`, dispatch `change` event
  3. Click `button` (Generate New Wallet)
- **Wait** 3s for key generation + bill rendering
- **Capture from `#walletDetails`:** `dest_address` (tb1p...), `dest_bill_wif` (TWEAKED PRIVATE KEY WIF)

### Step 2: Sweep Funded Address → Destination
- **Page:** Sweep (`/sweep.html`)
- **Actions (Step 1 - Enter Key):**
  1. `#wifInput` — set to `funded_wif`
  2. Select address type radio based on `funded_address` prefix:
     - `tb1q...` → `input[name="addrType"][value="segwit"]`
     - `tb1p...` → `input[name="addrType"][value="taproot_tweaked"]`
  3. `#networkSelect` — set to `testnet4`, dispatch `change`
  4. Click `#btnDerive`
- **Wait** 2s
- **Verify:** `#derivedAddress` text matches `funded_address`
- **Actions (Step 2 - Check Balance):**
  5. Click `#btnCheckBalance`
- **Wait** 3-5s (mempool.space API)
- **Verify:** Balance shows available tBTC
- **Actions (Step 3 - Sweep):**
  6. `#destAddress` — set to `dest_address`
  7. `#feeRate` — set to `2`, dispatch `input` event
  8. Click `#btnSweep`
- **Wait** 5s (signing + mempool.space broadcast)
- **Verify:** "Transaction Broadcast" message (not "Confirmed" — testnet4 blocks take ~10 min)
- **Capture:** `sweep_txid` (TRANSACTION ID), `sweep_amount` (AMOUNT SENT), `sweep_fee` (FEE)

### Step 3: Return Funds — Sweep Destination → Original Address
- **Note:** No need to wait for confirmation. The return sweep spends the unconfirmed output from Step 2.
- **Page:** Sweep (`/sweep.html`) — navigate fresh
- **Actions (Step 1 - Enter Key):**
  1. `#wifInput` — set to `dest_bill_wif`
  2. `input[name="addrType"][value="taproot_tweaked"]` — check (destination is always Taproot+backup)
  3. `#networkSelect` — set to `testnet4`, dispatch `change`
  4. Click `#btnDerive`
- **Wait** 2s
- **Verify:** `#derivedAddress` text matches `dest_address`
- **Actions (Step 2 - Check Balance):**
  5. Click `#btnCheckBalance`
- **Wait** 3-5s — balance should match `sweep_amount`
- **Actions (Step 3 - Sweep):**
  6. `#destAddress` — set to `funded_address`
  7. `#feeRate` — set to `2`, dispatch `input` event
  8. Click `#btnSweep`
- **Wait** 5s
- **Verify:** "Transaction Broadcast" message
- **Capture:** `return_txid`, `return_amount`, `return_fee`

### Step 4: Verify Fee Chain
- **Check:** `sweep_fee + return_fee` = total deducted from original funding
- **Check:** Funds are back at `funded_address` (minus fees)
- **Report:** Final summary table with explorer links

## Expected Result

| Step | From | To | Amount | Fee |
|------|------|----|--------|-----|
| Sweep | funded_address | dest_address | ~N sats | ~222 sats (at 2 sat/vB) |
| Return | dest_address | funded_address | ~N-fee sats | ~224 sats (at 2 sat/vB) |

Fee depends on address type: SegWit input ~111 vB, Taproot key-path ~112 vB.
At 2 sat/vB: SegWit sweep fee ~222 sats, Taproot sweep fee ~224 sats.

The return sweep spends an unconfirmed output (no confirmation wait needed). Both transactions are visible on https://mempool.space/testnet4. Print the explorer links for both TXIDs at the end so they can be traced back. Funds returned to original address (minus total fees).
