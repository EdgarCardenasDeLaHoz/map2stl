import matplotlib.pyplot as plt
import numpy as np 

def plot_data(im, name=None, bbox = None):

    print("--")


    if im is None:
        return
    
    plt.close("all")
    fig , axs = plt.subplots(1, 2, figsize=(12, 6), 
                    layout = 'tight', 
                    sharex=True, sharey=True)
    
    pcm = axs[0].imshow(im, cmap='terrain', extent=bbox)
    fig.colorbar(pcm, ax=axs[0] )

    pcm = axs[1].imshow(im, cmap='rainbow', extent=bbox)
    fig.colorbar(pcm, ax=axs[1])
    
    fig , axs = plt.subplots(1,2, figsize=(12, 6), 
                    layout = 'tight', 
                    sharex=True, sharey=True)
        
    pcm = axs[0].imshow(im, cmap='gray')

    a = -2

    for i in np.linspace(0, im.shape[0]-1, 10).astype(int):
        y = a*im[i]+i
        axs[0].axhline(i)
        axs[0].plot(y)

    for i in np.linspace(0, im.shape[1]-1, 10).astype(int):   
        y = a*im[:,i]+i
        axs[0].axvline(i)
        axs[0].plot(y,range(len(im[:,i])))


    for i in np.linspace(0, im.shape[0]-1, 10).astype(int):
        y = a*im[i]+i
        axs[1].axhline(i)
        axs[1].fill(y)

    for i in np.linspace(0, im.shape[1]-1, 10).astype(int):   
        y = a*im[:,i]+i
        axs[1].axvline(i)
        axs[1].plot(y,range(len(im[:,i])))
    
    plt.figure()
    plt.hist(im.ravel()[::10],100)

    if name is not None:
        fig.set_title(name)
        plt.suptitle(name)

