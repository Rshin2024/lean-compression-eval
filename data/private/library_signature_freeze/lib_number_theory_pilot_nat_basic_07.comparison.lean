

theorem _root_.CompressionEvalSignatureOriginal {C : ℕ → Sort*} {n m} (hnm : n ≤ m) (next : ∀ {k}, C k → C (k + 1))
    (Hnext : ∀ n, Surjective (@next n)) : Surjective (@leRecOn C n m hnm next) := by
  induction hnm with
  | refl =>
    intro x
    refine ⟨x, ?_⟩
    rw [leRecOn_self]
  | step hnm ih =>
    intro x
    obtain ⟨w, rfl⟩ := Hnext _ x
    obtain ⟨x, rfl⟩ := ih w
    refine ⟨x, ?_⟩
    rw [leRecOn_succ]

theorem _root_.CompressionEvalSignatureMaterialized {C : ℕ → Sort*} {n m} (hnm : n ≤ m) (next : ∀ {k}, C k → C (k + 1))
    (Hnext : ∀ n, Surjective (@next n)) : Surjective (@leRecOn C n m hnm next) := by
  induction hnm with
  | refl =>
    intro x
    refine ⟨x, ?_⟩
    rw [leRecOn_self]
  | step hnm ih =>
    intro x
    obtain ⟨w, rfl⟩ := Hnext _ x
    obtain ⟨x, rfl⟩ := ih w
    refine ⟨x, ?_⟩
    rw [leRecOn_succ]

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
