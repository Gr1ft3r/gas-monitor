import { createClient } from '@supabase/supabase-js'

// Supabase STRICTLY requires a full URL starting with https://
// window.location.origin grabs your live Vercel domain (e.g., https://gas-monitor-theta.vercel.app)
const supabaseUrl = import.meta.env.PROD 
  ? `${window.location.origin}/supabase-api` 
  : import.meta.env.VITE_SUPABASE_URL;

const supabaseKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

export const supabase = createClient(supabaseUrl, supabaseKey);