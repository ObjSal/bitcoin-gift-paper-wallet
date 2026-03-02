# UI Test: Chained Sweep → Recovery

## Overview
End-to-end UI test that drives the full fund flow through the website:
Faucet → Sweep → Recovery, across 3 Taproot+backup addresses.

**Run with:** Tell Claude Code: _"Run the UI test in test_ui_chained.md"_

## Prerequisites
- Bitcoin Core installed (`brew install bitcoin`)
- Start regtest server: `preview_start` with `bitcoin-gift-wallet-regtest`

## Test Steps

### Step 1: Generate Address 1 (Taproot + Backup)
- **Page:** Generator (`/`)
- **Actions:**
  1. Select **Taproot (P2TR)** address type
  2. Check **Enable Recovery Backup Key**
  3. Select **Regtest (Testing)** network
  4. Click **Generate New Wallet**
- **Screenshot checkpoint**
- **Capture:** `address_1`, `bill_wif_1` (tweaked WIF on bill)

### Step 2: Generate Address 2 (Taproot + Backup)
- **Page:** Generator (`/`)
- **Actions:**
  1. Keep Taproot + Backup Key + Regtest selected
  2. Click **Generate New Wallet**
- **Screenshot checkpoint**
- **Capture:** `address_2`, `bill_wif_2`, `backup_wif_2`, `internal_pubkey_2`

### Step 3: Generate Address 3 (Taproot + Backup)
- **Page:** Generator (`/`)
- **Actions:**
  1. Keep Taproot + Backup Key + Regtest selected
  2. Click **Generate New Wallet**
- **Screenshot checkpoint**
- **Capture:** `address_3`

### Step 4: Fund Address 1 via Faucet
- **Page:** Faucet (`/faucet.html`)
- **Actions:**
  1. Enter `address_1` in Bitcoin Address field
  2. Leave amount as 1.0 BTC
  3. Click **Fund Address**
- **Verify:** "Funded Successfully" message with txid and correct address
- **Screenshot checkpoint**

### Step 5: Sweep Address 1 → Address 2
- **Page:** Sweep (`/sweep.html`)
- **Actions (Step 1 - Enter Key):**
  1. Enter `bill_wif_1` in Private Key field
  2. Select **Taproot (tweaked)** address type
  3. Select **Regtest (Local Testing)** network
  4. Click **Derive Address**
- **Verify:** Derived address matches `address_1`
- **Screenshot checkpoint**
- **Actions (Step 2 - Check Balance):**
  5. Click **Check Balance**
- **Verify:** Balance shows ~1.0 BTC (100,000,000 sats)
- **Screenshot checkpoint**
- **Actions (Step 3 - Sweep):**
  6. Enter `address_2` in Destination Address field
  7. Click **Sweep All Funds**
- **Verify:** "Transaction Confirmed" with correct destination = `address_2`
- **Screenshot checkpoint**
- **Capture:** `sweep_amount`, `sweep_fee`

### Step 6: Recover Address 2 → Address 3
- **Page:** Recovery (`/recover.html`)
- **Actions (Step 1 - Enter Backup Key):**
  1. Enter `backup_wif_2` in Backup Private Key field
  2. Enter `internal_pubkey_2` in Internal Public Key field
  3. Select **Regtest (Local Testing)** network
  4. Click **Reconstruct Address**
- **Verify:** Reconstructed address matches `address_2`
- **Screenshot checkpoint**
- **Actions (Step 2 - Check Balance):**
  5. Click **Check Balance**
- **Verify:** Balance matches `sweep_amount`
- **Screenshot checkpoint**
- **Actions (Step 3 - Recover):**
  6. Enter `address_3` in Destination Address field
  7. Click **Recover Funds**
- **Verify:** "Transaction Confirmed" with correct destination = `address_3`
- **Screenshot checkpoint**
- **Capture:** `recover_amount`, `recover_fee`

### Step 7: Verify Fee Chain
- **Check:** `sweep_fee + recover_fee` = total deducted from 1.0 BTC
- **Check:** `recover_amount` = 100,000,000 - sweep_fee - recover_fee
- **Report:** Final summary table

## Expected Result

| Step | From | To | Amount | Fee |
|------|------|----|--------|-----|
| Faucet | coinbase | Address 1 | 1.0 BTC | — |
| Sweep | Address 1 | Address 2 | ~0.99999776 BTC | ~224 sats |
| Recover | Address 2 | Address 3 | ~0.99999454 BTC | ~322 sats |

All transactions confirmed on-chain. Total fees ~546 sats.
