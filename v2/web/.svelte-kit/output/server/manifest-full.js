export const manifest = (() => {
function __memo(fn) {
	let value;
	return () => value ??= (value = fn());
}

return {
	appDir: "_app",
	appPath: "_app",
	assets: new Set([]),
	mimeTypes: {},
	_: {
		client: {start:"_app/immutable/entry/start.xbbCWq_C.js",app:"_app/immutable/entry/app.CIKAv3Q8.js",imports:["_app/immutable/entry/start.xbbCWq_C.js","_app/immutable/chunks/DndITgbM.js","_app/immutable/chunks/DVFVzZBO.js","_app/immutable/chunks/MAAYEgMK.js","_app/immutable/entry/app.CIKAv3Q8.js","_app/immutable/chunks/x-27VF_l.js","_app/immutable/chunks/DndITgbM.js","_app/immutable/chunks/CbmB029U.js","_app/immutable/chunks/CF5nWnVj.js","_app/immutable/chunks/BOZJzwE_.js","_app/immutable/chunks/MAAYEgMK.js"],stylesheets:[],fonts:[],uses_env_dynamic_public:false},
		nodes: [
			__memo(() => import('./nodes/0.js')),
			__memo(() => import('./nodes/1.js')),
			__memo(() => import('./nodes/2.js'))
		],
		remotes: {
			
		},
		routes: [
			{
				id: "/",
				pattern: /^\/$/,
				params: [],
				page: { layouts: [0,], errors: [1,], leaf: 2 },
				endpoint: null
			}
		],
		prerendered_routes: new Set([]),
		matchers: async () => {
			
			return {  };
		},
		server_assets: {}
	}
}
})();
