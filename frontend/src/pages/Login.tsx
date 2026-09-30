import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import { ApiError } from "@/api/client";
import { Button, Field, Input, Notice } from "@/components/ui";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState(import.meta.env.VITE_DEMO_EMAIL ?? "");
  const [password, setPassword] = useState(import.meta.env.VITE_DEMO_PASSWORD ?? "");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await login(email, password);
      navigate("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-terrain-950 px-4">
      <form
        onSubmit={handleSubmit}
        aria-busy={isSubmitting}
        className="w-full max-w-sm animate-scale-in rounded-lg border border-terrain-800 bg-white p-8 shadow-elevated"
      >
        <div className="mb-6 flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-md bg-terrain-900 text-brand-400">
            <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4" aria-hidden="true">
              <path
                d="M12 3 2 8l10 5 10-5-10-5Z"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinejoin="round"
              />
              <path d="m2 13 10 5 10-5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              <path d="m2 18 10 5 10-5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">
            TERRAIN<span className="text-brand-600">-X</span>
          </h1>
        </div>
        <p className="mb-6 text-sm text-slate-500">Sign in to continue</p>

        {error && (
          <Notice id="login-error" tone="error" announce className="mb-4 animate-fade-in">
            {error}
          </Notice>
        )}

        <Field id="login-email" label="Email" required className="mb-4">
          {(fieldProps) => (
            <Input
              {...fieldProps}
              type="email"
              autoComplete="email"
              value={email}
              aria-describedby={[fieldProps["aria-describedby"], error ? "login-error" : undefined]
                .filter(Boolean)
                .join(" ") || undefined}
              onChange={(e) => setEmail(e.target.value)}
            />
          )}
        </Field>

        <Field id="login-password" label="Password" required className="mb-6">
          {(fieldProps) => (
            <Input
              {...fieldProps}
              type="password"
              autoComplete="current-password"
              value={password}
              aria-describedby={[fieldProps["aria-describedby"], error ? "login-error" : undefined]
                .filter(Boolean)
                .join(" ") || undefined}
              onChange={(e) => setPassword(e.target.value)}
            />
          )}
        </Field>

        <Button
          type="submit"
          loading={isSubmitting}
          loadingLabel="Signing in…"
          className="w-full"
        >
          Sign in
        </Button>

        <p className="mt-4 text-center text-sm text-slate-500">
          No account?{" "}
          <Link to="/register" className="font-medium text-brand-700 hover:underline">
            Register
          </Link>
        </p>
      </form>
    </main>
  );
}
