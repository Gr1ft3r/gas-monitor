import React, { useEffect, useState } from 'react';
import { supabase } from './supabase';

function App() {
  const [rawPrices, setRawPrices] = useState([]);
  const [selectedBrand, setSelectedBrand] = useState('All');
  const [expandedStationId, setExpandedStationId] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');

  const [stationName, setStationName] = useState('');
  const [cityName, setCityName] = useState('Baguio City');
  const [fuelType, setFuelType] = useState('');
  const [price, setPrice] = useState('');

  useEffect(() => {
    fetchPrices();
  }, []);

  async function fetchPrices() {
    const { data, error } = await supabase
      .from('prices')
      .select('*, stations!inner(id, name, status, city)')
      .neq('status', 'Archived')
      .neq('status', 'Pending_Admin')
      .neq('stations.status', 'Pending_Admin');

    if (!error) setRawPrices(data);
  }

  // Anti-Spam: Generate Fingerprint
  function getDeviceId() {
    let deviceId = localStorage.getItem('gas_monitor_device_id');
    if (!deviceId) {
      deviceId = 'device_' + Math.random().toString(36).substr(2, 9);
      localStorage.setItem('gas_monitor_device_id', deviceId);
    }
    return deviceId;
  }

  async function handleUpvotePrice(priceId, currentUpvotes, stationId, fuelType) {
    const deviceId = getDeviceId();
    const { error: logError } = await supabase.from('user_votes').insert([{ price_id: priceId, device_id: deviceId, vote_type: 'upvote' }]);
    if (logError && logError.code === '23505') {
      alert("Anti-Spam: You have already verified this price!");
      return;
    }

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

  async function handleProposePrice(stationId, fuelName) {
    const rawInput = prompt(`What is the new price for ${fuelName}?`);
    if (!rawInput) return;

    const isSuperAdmin = rawInput.endsWith('*');
    const newPrice = parseFloat(rawInput.replace('*', ''));

    if (!isNaN(newPrice) && newPrice > 30 && newPrice < 250) {
      if (isSuperAdmin) {
        await supabase.from('prices').update({ status: 'Archived' }).eq('station_id', stationId).eq('fuel_type', fuelName).eq('status', 'Verified');
      }
      await supabase.from('prices').insert([{
        station_id: stationId, fuel_type: fuelName, price: newPrice,
        status: isSuperAdmin ? 'Verified' : 'Unverified',
        upvotes: isSuperAdmin ? 3 : 0
      }]);
      fetchPrices();
      alert(isSuperAdmin ? "Super Admin: Price instantly verified!" : "Thanks! Your price update is now pending community verification.");
    } else {
      alert("❌ Blocked: Please enter a realistic fuel price (between ₱30 and ₱250).");
    }
  }

  async function handleOutOfStock(priceId, currentVotes) {
    const deviceId = getDeviceId();
    const { error: logError } = await supabase.from('user_votes').insert([{ price_id: priceId, device_id: deviceId, vote_type: 'out_of_stock' }]);
    if (logError && logError.code === '23505') {
      alert("Anti-Spam: You have already reported this as Out of Stock!");
      return;
    }
    const votes = currentVotes ? currentVotes : 0;
    await supabase.from('prices').update({ out_of_stock_votes: votes + 1 }).eq('id', priceId);
    fetchPrices();
    alert("Report received! If 2 more people report this, it will be marked Out of Stock.");
  }

  async function handleRetiredFuel(priceId, currentVotes) {
    const deviceId = getDeviceId();

    // 1. Anti-spam: Log the device vote
    const { error: logError } = await supabase.from('user_votes').insert([
      { price_id: priceId, device_id: deviceId, vote_type: 'retired' }
    ]);

    if (logError && logError.code === '23505') {
      alert("Anti-Spam: You have already reported this fuel as no longer sold!");
      return;
    }

    // 2. Calculate new vote total
    const votes = (currentVotes ? currentVotes : 0) + 1;

    // 3. Archive if threshold is met, otherwise just increment the counter
    if (votes >= 3) {
      await supabase.from('prices')
        .update({ status: 'Archived', retired_votes: votes })
        .eq('id', priceId);
      alert("Fuel removed! 3 users have confirmed this station no longer sells this fuel.");
    } else {
      await supabase.from('prices')
        .update({ retired_votes: votes })
        .eq('id', priceId);
      alert(`Report received! (${votes}/3) If ${3 - votes} more people report this, it will be removed.`);
    }

    fetchPrices();
  }

  async function handleAddStation(e) {
    e.preventDefault();
    const fullStationName = `${stationBrand} - ${branchName}`;

    const { data: existingStation } = await supabase.from('stations').select('id, status').ilike('name', fullStationName).limit(1);

    if (existingStation && existingStation.length > 0) {
      await supabase.from('prices').insert([{ station_id: existingStation[0].id, fuel_type: fuelType, price: parseFloat(price), status: 'Unverified' }]);
      alert(`Success! We found ${fullStationName} in our database. Your price is now pending community verification.`);
    } else {
      const { data: newStation } = await supabase.from('stations').insert([{ name: fullStationName, city: cityName, status: 'Pending_Admin' }]).select();
      if (newStation && newStation.length > 0) {
        await supabase.from('prices').insert([{ station_id: newStation[0].id, fuel_type: fuelType, price: parseFloat(price), status: 'Pending_Admin' }]);
        alert(`Thank you! ${fullStationName} is a new station. An administrator will review and approve it shortly to prevent spam.`);
      }
    }
    setBranchName(''); setPrice(''); setFuelType(''); fetchPrices();
  }

  // --- START OF DATA PREPARATION ---
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
      else if (st.name.includes('Flying V')) brand = 'Flying V';
      else if (st.name.includes('SeaOil')) brand = 'SeaOil';
      else if (st.name.includes('Phoenix')) brand = 'Phoenix';
      else if (st.name.includes('Unioil')) brand = 'Unioil';

      stationsMap[st.id] = { ...st, brand: brand, prices: [] };
    }
    stationsMap[st.id].prices.push(item);
  });

  let groupedStations = Object.values(stationsMap);

  if (selectedBrand !== 'All') {
    groupedStations = groupedStations.filter(s => s.brand === selectedBrand);
  }

  if (searchQuery && searchQuery.trim() !== '') {
    groupedStations = groupedStations.filter(s =>
      s.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      s.city.toLowerCase().includes(searchQuery.toLowerCase())
    );
  }

  groupedStations.sort((a, b) => a.name.localeCompare(b.name));
  // --- END OF DATA PREPARATION ---

  // UX UPGRADE: Calculate the most recent database activity
  let latestUpdate = 'Loading...';
  if (rawPrices.length > 0) {
    const dates = rawPrices.map(p => new Date(p.last_updated || Date.now()).getTime());
    const maxDate = new Date(Math.max(...dates));
    latestUpdate = maxDate.toLocaleString('en-PH', {
      weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'
    });
  }

  return (
    <div className="min-h-screen bg-gray-100 font-sans pb-10">
      {/* Your header and UI code continues here... */}

  // 1. Filter by Brand (if not 'All')
      if (selectedBrand !== 'All') {
        groupedStations = groupedStations.filter(s => s.brand === selectedBrand);
  }

      // 2. NEW: Filter by Search Query (if user typed something)
      if (searchQuery.trim() !== '') {
        groupedStations = groupedStations.filter(s =>
          s.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
          s.city.toLowerCase().includes(searchQuery.toLowerCase())
        );
  }

  // 3. Sort alphabetically
  groupedStations.sort((a, b) => a.name.localeCompare(b.name));

      const brands = ['All', 'Petron', 'Shell', 'Caltex', 'Cleanfuel', 'Flying V', 'SeaOil', 'Total', 'Phoenix', 'Unioil', 'Independent'];

      const fuelDictionary = {
        Petron: ['Blaze 100', 'XCS 95', 'Xtra Advance 93', 'Super Xtra 91', 'Turbo Diesel', 'Diesel Max', 'Gaas (Kerosene)'],
      Shell: ['V-Power Racing 98', 'V-Power Gasoline 95', 'FuelSave Unleaded 91', 'V-Power Diesel', 'Standard Diesel', 'Kerosene'],
      Caltex: ['Platinum 95', 'Silver 91', 'Diesel with Techron D'],
      Cleanfuel: ['Race 97', 'Premium 95', 'Clean 91', 'High-Performance Diesel', 'Auto LPG'],
      'Flying V': ['Rush 97', 'Thunder 95', 'Volt 91', 'Biodiesel'],
      SeaOil: ['Extreme 97', 'Extreme 95', 'Extreme U 91', 'Exceed Diesel'],
      Total: ['Excellium 95', 'Premier 91', 'Standard Diesel', 'Excellium Diesel'],
      Phoenix: ['Premium 97', 'Premium 95', 'Unleaded 91', 'E-Gas', 'Diesel'],
      Unioil: ['Premium 97', 'Premium 95', 'Unleaded 91', 'E-Gas', 'Diesel'],
      Default: ['Premium 95', 'Unleaded 91', 'Standard Diesel']
  };

      const [stationBrand, setStationBrand] = useState('Petron');
      const [branchName, setBranchName] = useState('');

      let availableFuels = fuelDictionary.Default;
      if (stationBrand === 'Petron') availableFuels = fuelDictionary.Petron;
      else if (stationBrand === 'Shell') availableFuels = fuelDictionary.Shell;
      else if (stationBrand === 'Caltex') availableFuels = fuelDictionary.Caltex;
      else if (stationBrand === 'Cleanfuel') availableFuels = fuelDictionary.Cleanfuel;
      else if (stationBrand === 'Flying V') availableFuels = fuelDictionary['Flying V'];
      else if (stationBrand === 'SeaOil') availableFuels = fuelDictionary.SeaOil;
      else if (stationBrand === 'Total') availableFuels = fuelDictionary.Total;

      // UX UPGRADE: Calculate the most recent database activity
      let latestUpdate = 'Loading...';
  if (rawPrices.length > 0) {
    const dates = rawPrices.map(p => new Date(p.last_updated || Date.now()).getTime());
      const maxDate = new Date(Math.max(...dates));
      latestUpdate = maxDate.toLocaleString('en-PH', {
        weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'
    });
  }

      return (
      <div className="min-h-screen bg-gray-100 font-sans pb-10">

        {/* UI UPGRADE: Changed "Real-time" to "Community-Driven" */}
        <header className="bg-blue-800 text-white p-4 shadow-md sticky top-0 z-20">
          <h1 className="text-xl font-bold">Benguet Gas Monitor</h1>
          <p className="text-xs text-blue-200">Community-Driven Pump Prices</p>
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

        {/* UX UPGRADE: Real-Time Search Bar */}
        <div className="bg-gray-100 px-4 py-3 sticky top-[105px] z-10 shadow-sm border-b border-gray-200 backdrop-blur-md bg-gray-100/90">
          <div className="relative max-w-md mx-auto">
            <span className="absolute inset-y-0 left-0 flex items-center pl-3 text-gray-400">
              🔍
            </span>
            <input
              type="text"
              placeholder="Search stations, branches, or cities..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full bg-white border border-gray-300 text-gray-900 text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block pl-10 p-2.5 shadow-inner"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                className="absolute inset-y-0 right-0 flex items-center pr-3 text-gray-400 hover:text-gray-600"
              >
                ✖
              </button>
            )}
          </div>
        </div>

        <main className="max-w-md mx-auto mt-4 px-4">

          {/* NEW UX UPGRADE: The Info & Disclaimer Banner */}
          <div className="bg-blue-50 border border-blue-200 p-4 rounded-lg mb-5 shadow-sm">
            <h2 className="text-blue-800 font-bold text-sm mb-1 flex items-center gap-1">
              <span>ℹ️</span> About This Data
            </h2>
            <p className="text-blue-900 text-xs leading-relaxed mb-3">
              Baseline prices are synced weekly with <strong>Department of Energy (DOE)</strong> advisories.
              Because actual pump prices vary by region due to logistics costs, this platform relies on
              <strong> local crowdsourcing</strong>. Help fellow drivers by verifying or updating prices when you fuel up!
            </p>
            <div className="bg-white/70 rounded px-2 py-1.5 border border-blue-100 inline-block">
              <p className="text-[10px] text-blue-800 font-bold uppercase tracking-wider">
                Last Database Update: <span className="text-blue-600 ml-1">{latestUpdate}</span>
              </p>
            </div>
          </div>

          <div className="flex flex-col gap-6">
            {/* UX UPGRADE: Group stations by city when 'All' is selected, or display normally if a specific brand is selected */}

            {['Baguio City', 'La Trinidad', 'Tuba'].map(city => {
              // Filter stations for the current city
              const cityStations = groupedStations.filter(s => s.city === city);

              // If there are no stations for this city under the current brand filter, skip rendering it
              if (cityStations.length === 0) return null;

              return (
                <div key={city} className="flex flex-col gap-3">

                  {/* City Section Header */}
                  <div className="flex items-center gap-2 pb-1 border-b-2 border-blue-800/10">
                    <span className="text-blue-800 text-lg">📍</span>
                    <h2 className="font-black text-gray-700 uppercase tracking-widest text-sm">
                      {city}
                    </h2>
                    <span className="ml-auto text-xs font-bold text-gray-400 bg-gray-200 px-2 py-0.5 rounded-full">
                      {cityStations.length}
                    </span>
                  </div>

                  {/* Render the Stations for this City */}
                  {cityStations.map(station => (
                    <div key={station.id} className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
                      <div onClick={() => setExpandedStationId(expandedStationId === station.id ? null : station.id)} className="p-4 flex justify-between items-center cursor-pointer hover:bg-gray-50">
                        <div>
                          <h3 className="font-bold text-gray-800 text-md">{station.name}</h3>
                          {/* We removed the redundant 📍 city tag here since it's now in the header */}
                          <p className="text-xs text-gray-400 font-medium mt-1">{station.prices.length} Fuel Types</p>
                        </div>
                        <div className="text-blue-600 font-bold text-xl">{expandedStationId === station.id ? '−' : '+'}</div>
                      </div>

                      {/* Fuel Types Accordion (Unchanged logic) */}
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
                                <button onClick={(e) => { e.stopPropagation(); handleUpvotePrice(fuel.id, fuel.upvotes, station.id, fuel.fuel_type); }} className="text-blue-600 text-xs font-bold px-2 py-1 hover:bg-blue-50 rounded">👍 Confirm</button>
                                <button onClick={(e) => { e.stopPropagation(); handleProposePrice(station.id, fuel.fuel_type); }} className="text-gray-600 text-xs font-bold px-2 py-1 hover:bg-gray-100 rounded">✏️ Update</button>
                                <button onClick={(e) => { e.stopPropagation(); handleOutOfStock(fuel.id, fuel.out_of_stock_votes); }} className="text-gray-500 hover:text-red-600 text-xs font-bold px-2 py-1 hover:bg-gray-100 rounded transition-colors">🚩 Report Empty</button>
                                <button onClick={(e) => { e.stopPropagation(); handleRetiredFuel(fuel.id, fuel.retired_votes); }} className="text-gray-500 hover:text-orange-600 text-xs font-bold px-2 py-1 hover:bg-gray-100 rounded transition-colors">🗑️ Not Sold</button>
                              </div>
                            </div>
                          ))}

                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setStationBrand(station.brand);
                              setBranchName(station.name.replace(`${station.brand} - `, '').trim());
                              window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
                            }}
                            className="w-full mt-1 py-2 text-xs font-bold text-blue-600 bg-blue-50 hover:bg-blue-100 rounded border border-blue-200 border-dashed transition-colors"
                          >
                            + Add Missing Fuel Type
                          </button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              );
            })}
          </div>

          {/* Add Station or Fuel Form */}
          <div className="mt-8 bg-white p-4 rounded-lg shadow-sm border border-gray-300">
            <h2 className="text-md font-bold text-gray-800 mb-1">Missing a Station or Fuel?</h2>
            <p className="text-xs text-gray-500 mb-3">Add it below to help the community.</p>

            <form onSubmit={handleAddStation} className="flex flex-col gap-2">
              <div className="flex gap-2">
                <select className="border p-2 rounded text-sm w-1/3 bg-gray-50 font-bold text-blue-800" value={stationBrand} onChange={(e) => { setStationBrand(e.target.value); setFuelType(''); }}>
                  <option value="Petron">Petron</option>
                  <option value="Shell">Shell</option>
                  <option value="Caltex">Caltex</option>
                  <option value="Cleanfuel">Cleanfuel</option>
                  <option value="Flying V">Flying V</option>
                  <option value="SeaOil">SeaOil</option>
                  <option value="Total">Total</option>
                  <option value="Independent">Independent</option>
                </select>
                <input type="text" placeholder="Branch (e.g. Loakan Road)" required className="border p-2 rounded text-sm w-2/3 bg-gray-50" value={branchName} onChange={(e) => setBranchName(e.target.value)} />
              </div>

              <select className="border p-2 rounded text-sm bg-gray-50" value={cityName} onChange={(e) => setCityName(e.target.value)}>
                <option>Baguio City</option>
                <option>La Trinidad</option>
                <option>Tuba</option>
              </select>

              <div className="flex gap-2">
                <select required className="border p-2 rounded text-sm w-1/2 bg-gray-50 text-gray-700" value={fuelType} onChange={(e) => setFuelType(e.target.value)}>
                  <option value="" disabled hidden>Select Fuel</option>
                  {availableFuels.map(f => <option key={f} value={f}>{f}</option>)}
                </select>
                <input type="number" step="0.01" placeholder="Price (₱)" required className="border p-2 rounded text-sm w-1/2 bg-gray-50" value={price} onChange={(e) => setPrice(e.target.value)} />
              </div>

              <button type="submit" className="bg-blue-800 text-white font-bold py-2 rounded mt-2 hover:bg-blue-900">
                Submit Addition
              </button>
            </form>
          </div>

        </main>
      </div>
      );
}

      export default App;