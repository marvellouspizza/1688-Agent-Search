export class PurchaseProviderError extends Error {
  override readonly name: string = "PurchaseProviderError";
}

export class PurchaseProviderInterrupted extends PurchaseProviderError {
  override readonly name: string = "PurchaseProviderInterrupted";
}

export class PurchaseInvalidResponse extends PurchaseProviderError {
  override readonly name: string = "PurchaseInvalidResponse";
}
