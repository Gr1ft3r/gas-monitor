import React, { useEffect, useState } from 'react';
import { supabase } from './supabase';

// Anti-Spam: Generate a permanent anonymous fingerprint for this device
function getDeviceId() {
  let deviceId = localStorage.getItem('gas_monitor_device_id');
  if (!deviceId) {
    deviceId = 'device_' + Math.random().toString(36).substr(2, 9);
    localStorage.setItem('gas_monitor_device_id', deviceId);
  }
  return deviceId;
}

function App() {
  const [rawPrices, setRawPrices] = useState([]);
  const [selectedBrand, setSelectedBrand] = useState('All');
  const [expandedStationId, setExpandedStationId] = useState(null);

  const [stationName, setStationName] = useState('');
  const [cityName, setCityName] = useState('Baguio City');
  const [fuelType, setFuelType] = useState('');
  const [price, setPrice] = useState('');
  const [stationBrand, setStationBrand] = useState('Petron');
  const [branchName, setBranchName] = useState(''); // Just the branch, e.g., 'Loakan Road'

  useEffect(() => {
    fetchPrices();
  }, []);

  async function fetchPrices() {
    const { data, error } = await supabase
      .from('prices')
      .select('*, stations!inner(id, name, status, city)') // Use !inner to filter the parent table
      .neq('status', 'Archived')
      .neq('status', 'Pending_Admin') // Hide admin-quarantined prices
      .neq('stations.status', 'Pending_Admin'); // Hide admin-quarantined stations
      
    if (!error) setRawPrices(data);
  }

  // Upgraded: Auto-Archive old prices when a new one gets Verified
  async function handleUpvotePrice(priceId, currentUpvotes, stationId, fuelType) {
    const deviceId = getDeviceId();

    // 1. Try to log the vote in the anti-spam table first
    const { error: logError } = await supabase
      .from('user_votes')
      .insert([{ price_id: priceId, device_id: deviceId, vote_type: 'upvote' }]);

    // 2. If it fails (because of the UNIQUE constraint), the user already voted!
    if (logError && logError.code === '23505') {
      alert("Anti-Spam: You have already verified this price!");
      return; // Stop the function here
    }

    // 3. If it succeeded, apply the math and archiving logic as normal
    const newUpvoteCount = currentUpvotes + 1;
    const isNowVerified = newUpvoteCount >= 3;

    if (isNowVerified) {
      await supabase.from('prices').update({ status: 'Archived' }).eq('station_id', stationId).eq('fuel_type', fuelType).eq('status', 'Verified');
      await supabase.from('prices').update({ upvotes: newUpvoteCount, status: 'Verified' }).eq('id', priceId);
    } else {
      await supabase.from('prices').update({ upvotes: newUpvoteCount }).eq('id', priceId);
    }
    fetchPrices();
  }

  // Upgraded: Super Admin bypass now also auto-archives the old price
  async function handleProposePrice(stationId, fuelName) {
    const rawInput = prompt(`What is the new price for ${fuelName}?`);
    if (!rawInput) return;

    const isSuperAdmin = rawInput.endsWith('*');
    const newPrice = parseFloat(rawInput.replace('*', ''));
    
    if (!isNaN(newPrice) && newPrice > 30 && newPrice < 250) {
      
      if (isSuperAdmin) {
        // If Super Admin, instantly archive the old price first
        await supabase
          .from('prices')
          .update({ status: 'Archived' })
          .eq('station_id', stationId)
          .eq('fuel_type', fuelName)
          .eq('status', 'Verified');
      }

      await supabase.from('prices').insert([{
        station_id: stationId,
        fuel_type: fuelName,
        price: newPrice,
        status: isSuperAdmin ? 'Verified' : 'Unverified',
        upvotes: isSuperAdmin ? 3 : 0
      }]);
      
      fetchPrices();
      alert(isSuperAdmin ? "Super Admin: Price instantly verified!" : "Thanks! Your price update is now pending community verification.");
    } else {
      alert("❌ Blocked: Please enter a realistic fuel price (between ₱30 and ₱250).");
    }
  }

  // Out of Stock Logic
  async function handleOutOfStock(priceId, currentVotes) {
    const deviceId = getDeviceId();

    // 1. Check if they already reported this
    const { error: logError } = await supabase
      .from('user_votes')
      .insert([{ price_id: priceId, device_id: deviceId, vote_type: 'out_of_stock' }]);

    if (logError && logError.code === '23505') {
      alert("Anti-Spam: You have already reported this as Out of Stock!");
      return; 
    }

    // 2. Apply the vote
    const votes = currentVotes ? currentVotes : 0;
    await supabase.from('prices').update({ out_of_stock_votes: votes + 1 }).eq('id', priceId);
    fetchPrices();
    alert("Report received! If 2 more people report this, it will be marked Out of Stock.");
  }

  async function handleAddStation(e) {
    e.preventDefault();
    const fullStationName = `${stationBrand} - ${branchName}`;

    // 1. CHECK IF THE STATION ALREADY EXISTS IN THE DATABASE
    const { data: existingStation } = await supabase
      .from('stations')
      .select('id, status')
      .ilike('name', fullStationName) // Case-insensitive check
      .limit(1);

    if (existingStation && existingStation.length > 0) {
      // SCENARIO A: The station exists! Just add the new price to it.
      await supabase.from('prices').insert([{ 
        station_id: existingStation[0].id, 
        fuel_type: fuelType, 
        price: parseFloat(price), 
        status: 'Unverified' // Wait for 3 community upvotes
      }]);
      alert(`Success! We found ${fullStationName} in our database. Your price is now pending community verification.`);
      
    } else {
      // SCENARIO B: Brand new station! Send to Admin Quarantine.
      const { data: newStation } = await supabase.from('stations').insert([{ 
        name: fullStationName, 
        city: cityName, 
        status: 'Pending_Admin' // Hides it from the public app
      }]).select();

      if (newStation && newStation.length > 0) {
        await supabase.from('prices').insert([{ 
          station_id: newStation[0].id, 
          fuel_type: fuelType, 
          price: parseFloat(price), 
          status: 'Pending_Admin' 
        }]);
        alert(`Thank you! ${fullStationName} is a new station. An administrator will review and approve it shortly to prevent spam.`);
      }
    }

    // Clear the form and refresh
    setBranchName(''); setPrice(''); setFuelType(''); fetchPrices();
  }

  const stationsMap = {};
  rawPrices.forEach(item => {
    const st = item.stations;
    if (!stationsMap[st.id]) {
      let brand = 'Independent';
      if (st.name.includes('Petron')) brand = 'Petron';
      else if (st.name.includes('Shell')) brand = 'Shell';
      else if (st.name.includes('Caltex')) brand = 'Caltex';
      else if (st.name.includes('Cleanfuel')) brand = 'Cleanfuel';
      else if (st.name.includes('Total')) brand = 'Total';
      stationsMap[st.id] = { ...st, brand: brand, prices: [] };
    }
    stationsMap[st.id].prices.push(item);
  });

  let groupedStations = Object.values(stationsMap);
  if (selectedBrand !== 'All') groupedStations = groupedStations.filter(s => s.brand === selectedBrand);
  groupedStations.sort((a, b) => a.name.localeCompare(b.name));

  const brands = ['All', 'Petron', 'Shell', 'Caltex', 'Cleanfuel', 'Total', 'Independent'];

  // Smart Dropdown Dictionary (Now including Flying V, SeaOil, and Total)
  const fuelDictionary = {
    Petron: ['Blaze 100', 'XCS 95', 'Xtra Advance 93', 'Super Xtra 91', 'Turbo Diesel', 'Diesel Max', 'Gaas (Kerosene)'],
    Shell: ['V-Power Racing 98', 'V-Power Gasoline 95', 'FuelSave Unleaded 91', 'V-Power Diesel', 'Standard Diesel', 'Kerosene'],
    Caltex: ['Platinum 95', 'Silver 91', 'Diesel with Techron D'],
    Cleanfuel: ['Race 97', 'Premium 95', 'Clean 91', 'High-Performance Diesel', 'Auto LPG'],
    'Flying V': ['Rush 97', 'Thunder 95', 'Volt 91', 'Biodiesel'],
    SeaOil: ['Extreme 97', 'Extreme 95', 'Extreme U 91', 'Exceed Diesel'],
    Total: ['Excellium 95', 'Premier 91', 'Standard Diesel', 'Excellium Diesel'],
    Default: ['Premium 95', 'Unleaded 91', 'Standard Diesel'] 
  };

  // Auto-detect which fuels to show based on the brand dropdown selection
  let availableFuels = fuelDictionary.Default;
  if (stationBrand === 'Petron') availableFuels = fuelDictionary.Petron;
  else if (stationBrand === 'Shell') availableFuels = fuelDictionary.Shell;
  else if (stationBrand === 'Caltex') availableFuels = fuelDictionary.Caltex;
  else if (stationBrand === 'Cleanfuel') availableFuels = fuelDictionary.Cleanfuel;
  else if (stationBrand === 'Flying V') availableFuels = fuelDictionary['Flying V'];
  else if (stationBrand === 'SeaOil') availableFuels = fuelDictionary.SeaOil;
  else if (stationBrand === 'Total') availableFuels = fuelDictionary.Total;

  return (
    <div className="min-h-screen bg-gray-100 font-sans pb-10">

      <header className="bg-blue-800 text-white p-4 shadow-md sticky top-0 z-20">
        <h1 className="text-xl font-bold">Benguet Gas Monitor</h1>
        <p className="text-xs text-blue-200">Real-time pump prices</p>
      </header>

      <div className="bg-white shadow-sm border-b border-gray-200 p-3 overflow-x-auto whitespace-nowrap sticky top-[60px] z-10">
        <div className="flex gap-2">
          {brands.map(brand => (
            <button key={brand} onClick={() => { setSelectedBrand(brand); setExpandedStationId(null); }}
              className={`px-4 py-1.5 rounded-full text-sm font-bold border transition-colors ${selectedBrand === brand ? 'bg-blue-800 text-white border-blue-800' : 'bg-gray-50 text-gray-600 border-gray-300 hover:bg-gray-200'
                }`}>
              {brand}
            </button>
          ))}
        </div>
      </div>

      <main className="max-w-md mx-auto mt-4 px-4">
        <div className="flex flex-col gap-3">
          {groupedStations.map(station => (
            <div key={station.id} className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">

              <div onClick={() => setExpandedStationId(expandedStationId === station.id ? null : station.id)} className="p-4 flex justify-between items-center cursor-pointer hover:bg-gray-50">
                <div>
                  <h3 className="font-bold text-gray-800 text-md">{station.name}</h3>
                  <p className="text-xs text-gray-500 mt-1">📍 {station.city}</p>
                </div>
                <div className="text-gray-400 font-bold text-xl">{expandedStationId === station.id ? '−' : '+'}</div>
              </div>

              {expandedStationId === station.id && (
                <div className="bg-gray-50 border-t border-gray-200 p-4 flex flex-col gap-3">
                  {station.prices.sort((a, b) => a.price - b.price).map(fuel => (
                    <div key={fuel.id} className="flex flex-col bg-white p-3 rounded border border-gray-200 shadow-sm">

                      <div className="flex justify-between items-start">
                        <div>
                          <p className="font-bold text-blue-800 text-sm flex items-center gap-2">
                            {fuel.fuel_type}
                            <span className={`text-[9px] px-1.5 py-0.5 rounded-full ${fuel.status === 'Verified' ? 'bg-green-100 text-green-800' : 'bg-gray-200 text-gray-600'}`}>
                              {fuel.status}
                            </span>
                          </p>
                          <p className="text-xs text-gray-500 mt-1">{fuel.upvotes} Upvotes</p>
                        </div>

                        <div className="text-right">
                          {fuel.out_of_stock_votes >= 3 ? (
                            <p className="text-xl font-black text-red-600 line-through">OUT OF STOCK</p>
                          ) : (
                            <p className="text-xl font-black text-gray-900">₱{fuel.price.toFixed(2)}</p>
                          )}
                        </div>
                      </div>

                      <div className="flex justify-between mt-3 pt-2 border-t border-gray-100">
                        <button onClick={(e) => {e.stopPropagation(); handleUpvotePrice(fuel.id, fuel.upvotes, station.id, fuel.fuel_type); }} className="text-blue-600 text-xs font-bold px-2 py-1 hover:bg-blue-50 rounded">👍 Confirm</button>
                        <button onClick={(e) => { e.stopPropagation(); handleProposePrice(station.id, fuel.fuel_type); }} className="text-gray-600 text-xs font-bold px-2 py-1 hover:bg-gray-100 rounded">✏️ Update</button>
                        <button onClick={(e) => { e.stopPropagation(); handleOutOfStock(fuel.id, fuel.out_of_stock_votes); }} className="text-gray-500 text-xs font-bold px-2 py-1 hover:bg-gray-100 hover:text-red-600 rounded transition-colors">🚩 Report Empty</button>
                      </div>

                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>

        {/* The Upgraded Add Station Form with Brand Dropdown */}
        <div className="mt-8 bg-white p-4 rounded-lg shadow-sm border border-gray-300">
          <h2 className="text-md font-bold text-gray-800 mb-3">Missing a Station?</h2>
          <form onSubmit={handleAddStation} className="flex flex-col gap-2">

            {/* NEW: Brand Selection Dropdown */}
            <div className="flex gap-2">
              <select
                className="border p-2 rounded text-sm w-1/3 bg-gray-50 font-bold text-blue-800"
                value={stationBrand}
                onChange={(e) => {
                  setStationBrand(e.target.value);
                  setFuelType(''); // Reset the fuel type when they change brands
                }}
              >
                <option value="Petron">Petron</option>
                <option value="Shell">Shell</option>
                <option value="Caltex">Caltex</option>
                <option value="Cleanfuel">Cleanfuel</option>
                <option value="Flying V">Flying V</option>
                <option value="SeaOil">SeaOil</option>
                <option value="Total">Total</option>
                <option value="Independent">Independent (Eco Fill, etc.)</option>
              </select>

              {/* The Station Branch Name */}
              <input
                type="text" placeholder="Branch (e.g. Loakan Road)" required
                className="border p-2 rounded text-sm w-2/3 bg-gray-50"
                value={branchName}
                onChange={(e) => setBranchName(e.target.value)}
              />
            </div>

            <select className="border p-2 rounded text-sm bg-gray-50" value={cityName} onChange={(e) => setCityName(e.target.value)}>
              <option>Baguio City</option><option>La Trinidad</option><option>Tuba</option>
            </select>

            <div className="flex gap-2">
              {/* The Fuel Dropdown - Now strictly controlled by the Brand dropdown! */}
              <select required className="border p-2 rounded text-sm w-1/2 bg-gray-50 text-gray-700" value={fuelType} onChange={(e) => setFuelType(e.target.value)}>
                <option value="" disabled hidden>Select Fuel</option>
                {fuelDictionary[stationBrand === 'Independent' ? 'Default' : stationBrand]?.map(f => (
                  <option key={f} value={f}>{f}</option>
                ))}
              </select>

              <input type="number" step="0.01" placeholder="Price (₱)" required className="border p-2 rounded text-sm w-1/2 bg-gray-50" value={price} onChange={(e) => setPrice(e.target.value)} />
            </div>

            <button type="submit" className="bg-blue-800 text-white font-bold py-2 rounded mt-2 hover:bg-blue-900">Submit Station</button>
          </form>
        </div>

      </main>
    </div>
  );
}

export default App;