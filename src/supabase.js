import { createClient } from '@supabase/supabase-js'

// If the app is live (PROD), use the Vercel proxy. 
// If running locally on your computer, use the direct URL.
const supabaseUrl = import.meta.env.PROD 
  ? '/supabase-api' 
  : import.meta.env.VITE_SUPABASE_URL;

const supabaseKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

export const supabase = createClient(supabaseUrl, supabaseKey);