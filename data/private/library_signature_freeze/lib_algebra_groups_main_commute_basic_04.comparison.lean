

theorem _root_.CompressionEvalSignatureOriginal (h : Commute a b) : Function.Commute (· * a) (· * b) := SemiconjBy.function_semiconj_mul_right_swap h

theorem _root_.CompressionEvalSignatureMaterialized {«G» : Type u_1} [«inst» : Semigroup.{u_1} G] {«a» : G} {«b» : G} (h : Commute a b) : Function.Commute (· * a) (· * b) := SemiconjBy.function_semiconj_mul_right_swap h

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
