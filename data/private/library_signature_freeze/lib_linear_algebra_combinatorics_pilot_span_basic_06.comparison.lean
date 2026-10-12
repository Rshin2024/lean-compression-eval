

theorem _root_.CompressionEvalSignatureOriginal {V : Submodule R M} (f g : V →ₗ[R] N)
    {S : Set M} (hV : V = span R S) : f = g ↔ ∀ (s : S), f ⟨s, by simpa only [hV] using! subset_span (by simp)⟩ =
      g ⟨s, by simpa only [hV] using! subset_span (by simp)⟩ := by
  constructor
  · rintro rfl _
    rfl
  · intro h
    subst hV
    suffices ∀ (x : M) (hx : x ∈ span R S), f ⟨x, hx⟩ = g ⟨x, hx⟩ by
      ext ⟨x, hx⟩
      exact this x hx
    intro x hx
    induction hx using span_induction with
    | mem x hx => exact h ⟨x, hx⟩
    | zero => erw [map_zero, map_zero]
    | add x y hx hy hx' hy' =>
        erw [f.map_add ⟨x, hx⟩ ⟨y, hy⟩, g.map_add ⟨x, hx⟩ ⟨y, hy⟩]
        rw [hx', hy']
    | smul a x hx hx' =>
        erw [f.map_smul a ⟨x, hx⟩, g.map_smul a ⟨x, hx⟩]
        rw [hx']

theorem _root_.CompressionEvalSignatureMaterialized {«R» : Type u_1} {«M» : Type u_4} [«inst» : Semiring.{u_1} R] [«inst_1» : AddCommMonoid.{u_4} M] [«inst_2» : Module.{u_1, u_4} R M] {«N» : Type u_8} [«inst_3» : AddCommMonoid.{u_8} N] [«inst_4» : Module.{u_1, u_8} R N] {V : Submodule R M} (f g : V →ₗ[R] N)
    {S : Set M} (hV : V = span R S) : f = g ↔ ∀ (s : S), f ⟨s, by simpa only [hV] using! subset_span (by simp)⟩ =
      g ⟨s, by simpa only [hV] using! subset_span (by simp)⟩ := by
  constructor
  · rintro rfl _
    rfl
  · intro h
    subst hV
    suffices ∀ (x : M) (hx : x ∈ span R S), f ⟨x, hx⟩ = g ⟨x, hx⟩ by
      ext ⟨x, hx⟩
      exact this x hx
    intro x hx
    induction hx using span_induction with
    | mem x hx => exact h ⟨x, hx⟩
    | zero => erw [map_zero, map_zero]
    | add x y hx hy hx' hy' =>
        erw [f.map_add ⟨x, hx⟩ ⟨y, hy⟩, g.map_add ⟨x, hx⟩ ⟨y, hy⟩]
        rw [hx', hy']
    | smul a x hx hx' =>
        erw [f.map_smul a ⟨x, hx⟩, g.map_smul a ⟨x, hx⟩]
        rw [hx']

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
